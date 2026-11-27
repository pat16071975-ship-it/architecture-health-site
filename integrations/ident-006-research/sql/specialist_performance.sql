WITH limits AS (
    SELECT CAST(@date_from AS datetime2) AS month_start,
           CAST(@fact_end AS datetime2) AS fact_end,
           CAST(@date_to AS datetime2) AS month_end
), work_slots AS (
    SELECT DISTINCT c.ID_Staffs AS staff_id,c.DateOfWork,t.TimeStartValue,t.TimeEndValue
    FROM dbo.CurrentTimeTable c
    JOIN dbo.Times t ON t.ID=c.ID_Times
    CROSS JOIN limits l
    WHERE c.DateOfWork>=l.month_start AND c.DateOfWork<l.month_end
      AND c.ID_Staffs IS NOT NULL AND c.IsWorkingTime=1
      AND c.IsHeaderCell=0
), visit_rows AS (
    SELECT r.ID_Staffs AS staff_id,r.PlanStart,r.PlanEnd,r.ReceptionStarted,r.ReceptionEnded,
           r.PatientAppeared,r.ReceptionCanceled,r.ID_ReceptionCancelReasons
    FROM dbo.Receptions r CROSS JOIN limits l
    WHERE r.PlanStart>=l.month_start AND r.PlanStart<l.month_end
), intervals AS (
    SELECT 'schedule_seconds' AS metric,w.staff_id,
        DATEADD(second,DATEDIFF(second,CAST('00:00:00' AS time),w.TimeStartValue),CAST(w.DateOfWork AS datetime2)) AS s,
        DATEADD(second,DATEDIFF(second,CAST('00:00:00' AS time),w.TimeEndValue),CAST(w.DateOfWork AS datetime2)) AS e
    FROM work_slots w WHERE w.TimeEndValue>w.TimeStartValue
    UNION ALL
    SELECT 'actual_seconds',v.staff_id,
        CASE WHEN v.ReceptionStarted<l.month_start THEN l.month_start ELSE CAST(v.ReceptionStarted AS datetime2) END,
        CASE WHEN v.ReceptionEnded>l.fact_end THEN l.fact_end ELSE CAST(v.ReceptionEnded AS datetime2) END
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
      AND v.ReceptionStarted IS NOT NULL AND v.ReceptionEnded IS NOT NULL
      AND v.ReceptionEnded>v.ReceptionStarted
      AND v.ReceptionEnded<=l.fact_end
      AND v.ReceptionStarted<l.fact_end AND v.ReceptionEnded>l.month_start
      AND v.PlanStart<l.fact_end
    UNION ALL
    SELECT 'booked_seconds',v.staff_id,CAST(v.PlanStart AS datetime2),
        CASE WHEN v.PlanEnd>l.month_end THEN l.month_end ELSE CAST(v.PlanEnd AS datetime2) END
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.PlanStart>=l.fact_end AND v.PlanStart<l.month_end
      AND v.PlanEnd>v.PlanStart
      AND v.ReceptionStarted IS NULL AND v.ReceptionEnded IS NULL
      AND v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
), unique_intervals AS (
    SELECT DISTINCT metric,staff_id,s,e FROM intervals WHERE e>s
), ordered_intervals AS (
    SELECT metric,staff_id,s,e,
        MAX(e) OVER(PARTITION BY metric,staff_id ORDER BY s,e ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_end
    FROM unique_intervals
), islands AS (
    SELECT metric,staff_id,s,e,
        SUM(CASE WHEN prev_end IS NULL OR s>prev_end THEN 1 ELSE 0 END)
        OVER(PARTITION BY metric,staff_id ORDER BY s,e ROWS UNBOUNDED PRECEDING) AS grp
    FROM ordered_intervals
), merged AS (
    SELECT metric,staff_id,grp,MIN(s) AS s,MAX(e) AS e
    FROM islands GROUP BY metric,staff_id,grp

), duration_totals AS (
    SELECT metric,staff_id,SUM(CONVERT(bigint,DATEDIFF(second,s,e))) AS seconds
    FROM merged GROUP BY metric,staff_id
), visit_counts AS (
    SELECT v.staff_id,
      SUM(CONVERT(bigint,CASE WHEN v.PlanStart<l.fact_end AND v.ReceptionStarted IS NOT NULL
        AND v.ReceptionEnded>v.ReceptionStarted AND v.ReceptionEnded<=l.fact_end
        AND v.ReceptionStarted<l.fact_end AND v.ReceptionEnded>l.month_start THEN 1 ELSE 0 END)) AS completed_visits,
      SUM(CONVERT(bigint,CASE WHEN v.PlanStart>=l.fact_end AND v.PlanEnd>v.PlanStart
        AND v.ReceptionStarted IS NULL AND v.ReceptionEnded IS NULL THEN 1 ELSE 0 END)) AS booked_visits,
      SUM(CONVERT(bigint,CASE WHEN v.PlanStart<l.fact_end AND v.PatientAppeared IS NOT NULL
        AND (v.ReceptionStarted IS NULL OR v.ReceptionEnded IS NULL OR v.ReceptionEnded<=v.ReceptionStarted OR v.ReceptionEnded>l.fact_end)
        THEN 1 ELSE 0 END)) AS appeared_incomplete
    FROM visit_rows v CROSS JOIN limits l
    WHERE v.ReceptionCanceled IS NULL AND v.ID_ReceptionCancelReasons IS NULL
    GROUP BY v.staff_id
), service_rows AS (
    SELECT j.ID_Staffs AS staff_id,j.AmountWithDiscount,o.ID AS order_found,
           CASE WHEN r.ReceptionCanceled IS NULL AND r.ID_ReceptionCancelReasons IS NULL
                  AND r.ID_Staffs=j.ID_Staffs AND r.PlanStart>=l.month_start AND r.PlanStart<l.fact_end
                  AND r.ReceptionStarted IS NOT NULL AND r.ReceptionEnded>r.ReceptionStarted
                  AND r.ReceptionEnded<=l.fact_end AND r.ReceptionStarted<l.fact_end
                  AND r.ReceptionEnded>l.month_start
                THEN 1 ELSE 0 END AS linked_completed
    FROM dbo.RenderedServicesJournal j CROSS JOIN limits l
    LEFT JOIN dbo.Orders o ON o.ID=j.ID_Orders
    LEFT JOIN dbo.Receptions r ON r.ID=o.ID_Receptions
    WHERE j.DateTimeOrder>=l.month_start AND j.DateTimeOrder<l.fact_end
), financial AS (
    SELECT staff_id,
        SUM(CONVERT(decimal(28,6),ISNULL(AmountWithDiscount,0))) AS net_amount,
        SUM(CONVERT(decimal(28,6),CASE WHEN linked_completed=1 THEN ISNULL(AmountWithDiscount,0) ELSE 0 END)) AS linked_amount,
        COUNT_BIG(*) AS service_rows_count,
        SUM(CONVERT(bigint,CASE WHEN AmountWithDiscount IS NULL THEN 1 ELSE 0 END)) AS null_amount_count,
        SUM(CONVERT(bigint,CASE WHEN order_found IS NULL THEN 1 ELSE 0 END)) AS missing_order_count
    FROM service_rows GROUP BY staff_id
), staff_ids AS (
    SELECT staff_id FROM work_slots UNION SELECT staff_id FROM visit_counts UNION SELECT staff_id FROM financial
), reserves AS (
    SELECT DISTINCT c.ID_Staffs AS staff_id,c.DateOfWork,t.TimeStartValue,t.TimeEndValue,
        DATEDIFF(second,t.TimeStartValue,t.TimeEndValue) AS seconds
    FROM dbo.CurrentTimeTable c CROSS JOIN limits l JOIN dbo.Times t ON t.ID=c.ID_Times
    WHERE c.DateOfWork>=l.month_start AND c.DateOfWork<l.month_end
      AND c.ID_Staffs IS NOT NULL AND c.IsWorkingTime=1 AND c.IsHeaderCell=0 AND c.ID_TimeReserves IS NOT NULL
      AND t.TimeEndValue>t.TimeStartValue
), reserve_totals AS (
    SELECT staff_id,SUM(CONVERT(bigint,seconds)) AS seconds FROM reserves GROUP BY staff_id
)
SELECT a.staff_id AS staff_id,
    LEFT(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(p.Surname))+N' '+LTRIM(RTRIM(p.Name))+N' '+LTRIM(RTRIM(p.Patronymic)),NCHAR(13),N' '),NCHAR(10),N' '),NCHAR(9),N' '),120) AS staff_name,
    s.Archive AS archived,
    ISNULL(g.seconds,CONVERT(bigint,0)) AS schedule_seconds,
    ISNULL(f.seconds,CONVERT(bigint,0)) AS actual_seconds,
    ISNULL(b.seconds,CONVERT(bigint,0)) AS booked_seconds,
    ISNULL(z.seconds,CONVERT(bigint,0)) AS reserved_seconds,
    ISNULL(v.completed_visits,CONVERT(bigint,0)) AS completed_visits,
    ISNULL(v.booked_visits,CONVERT(bigint,0)) AS booked_visits,
    ISNULL(v.appeared_incomplete,CONVERT(bigint,0)) AS appeared_incomplete,
    CONVERT(decimal(28,6),ISNULL(m.net_amount,0)) AS revenue_total,
    CONVERT(decimal(28,6),ISNULL(m.linked_amount,0)) AS revenue_linked_completed,
    ISNULL(m.service_rows_count,CONVERT(bigint,0)) AS service_rows,
    ISNULL(m.null_amount_count,CONVERT(bigint,0)) AS null_amount_count,
    ISNULL(m.missing_order_count,CONVERT(bigint,0)) AS missing_order_count
FROM staff_ids a
LEFT JOIN dbo.Staffs s ON s.ID_Persons=a.staff_id
LEFT JOIN dbo.Persons p ON p.ID=s.ID_Persons
LEFT JOIN duration_totals g ON g.staff_id=a.staff_id AND g.metric='schedule_seconds'
LEFT JOIN duration_totals f ON f.staff_id=a.staff_id AND f.metric='actual_seconds'
LEFT JOIN duration_totals b ON b.staff_id=a.staff_id AND b.metric='booked_seconds'
LEFT JOIN reserve_totals z ON z.staff_id=a.staff_id
LEFT JOIN visit_counts v ON v.staff_id=a.staff_id
LEFT JOIN financial m ON m.staff_id=a.staff_id
ORDER BY a.staff_id
