WITH target_staff AS (
    SELECT s.ID_Persons AS staff_id,
        LEFT(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(p.Surname))+N' '+LTRIM(RTRIM(p.Name))+N' '+LTRIM(RTRIM(p.Patronymic)),NCHAR(13),N' '),NCHAR(10),N' '),NCHAR(9),N' '),120) AS staff_name
    FROM dbo.Staffs s JOIN dbo.Persons p ON p.ID=s.ID_Persons WHERE s.ID_Persons=@staff_id
), target_visits AS (
    SELECT r.ID AS reception_id,r.ID_Patients AS patient_id
    FROM dbo.Receptions r JOIN target_staff s ON s.staff_id=r.ID_Staffs
    WHERE r.PlanStart>=@date_from AND r.PlanStart<@fact_end
      AND r.ReceptionCanceled IS NULL AND r.ID_ReceptionCancelReasons IS NULL
      AND r.ReceptionStarted IS NOT NULL AND r.ReceptionEnded>r.ReceptionStarted
      AND r.ReceptionEnded<=@fact_end
), cohort AS (
    SELECT DISTINCT patient_id FROM target_visits
), ranked_history AS (
    SELECT r.ID AS reception_id,
        ROW_NUMBER() OVER(PARTITION BY r.ID_Patients,r.ID_Staffs ORDER BY r.ReceptionStarted,r.ID) AS doctor_visit_number,
        ROW_NUMBER() OVER(PARTITION BY r.ID_Patients ORDER BY r.ReceptionStarted,r.ID) AS clinic_visit_number
    FROM dbo.Receptions r JOIN cohort c ON c.patient_id=r.ID_Patients
    WHERE r.ReceptionCanceled IS NULL AND r.ID_ReceptionCancelReasons IS NULL
      AND r.ReceptionStarted IS NOT NULL AND r.ReceptionEnded>r.ReceptionStarted
      AND r.ReceptionEnded<=@observed
), rendered AS (
    SELECT o.ID_Receptions AS reception_id,o.ID AS order_found,j.AmountWithDiscount
    FROM dbo.RenderedServicesJournal j JOIN target_staff s ON s.staff_id=j.ID_Staffs
    LEFT JOIN dbo.Orders o ON o.ID=j.ID_Orders
    WHERE j.DateTimeOrder>=@date_from AND j.DateTimeOrder<@fact_end
), rendered_totals AS (
    SELECT ISNULL(SUM(CONVERT(decimal(28,6),ISNULL(AmountWithDiscount,0))),0) AS revenue,
        COUNT_BIG(*) AS service_rows,
        ISNULL(SUM(CONVERT(bigint,CASE WHEN AmountWithDiscount IS NULL THEN 1 ELSE 0 END)),0) AS null_count,
        ISNULL(SUM(CONVERT(bigint,CASE WHEN order_found IS NULL THEN 1 ELSE 0 END)),0) AS missing_order_count
    FROM rendered
), by_reception AS (
    SELECT reception_id,SUM(CONVERT(decimal(28,6),ISNULL(AmountWithDiscount,0))) AS revenue
    FROM rendered WHERE reception_id IS NOT NULL GROUP BY reception_id
), labeled AS (
    SELECT v.reception_id,v.patient_id,h.doctor_visit_number,h.clinic_visit_number,
        ISNULL(a.revenue,CONVERT(decimal(38,6),0)) AS revenue
    FROM target_visits v JOIN ranked_history h ON h.reception_id=v.reception_id
    LEFT JOIN by_reception a ON a.reception_id=v.reception_id
), split_visits AS (
    SELECT v.patient_id,v.revenue,k.category
    FROM labeled v CROSS APPLY (
      VALUES (CASE WHEN v.doctor_visit_number=1 THEN N'doctor_primary' ELSE N'doctor_repeat' END),
             (CASE WHEN v.clinic_visit_number=1 THEN N'clinic_primary' ELSE N'clinic_repeat' END)
    ) k(category)
), categories AS (
    SELECT N'doctor_primary' AS category UNION ALL SELECT N'doctor_repeat'
    UNION ALL SELECT N'clinic_primary' UNION ALL SELECT N'clinic_repeat'
), split_totals AS (
    SELECT category,COUNT_BIG(*) AS visits,COUNT_BIG(DISTINCT patient_id) AS patients,
        SUM(CONVERT(decimal(28,6),revenue)) AS revenue FROM split_visits GROUP BY category
), visit_totals AS (
    SELECT COUNT_BIG(*) AS visits,COUNT_BIG(DISTINCT patient_id) AS patients,
        ISNULL(SUM(CONVERT(decimal(28,6),revenue)),0) AS linked_revenue FROM labeled
), target_counts AS (
    SELECT COUNT_BIG(*) AS visits FROM target_visits
), combined AS (
    SELECT c.category,ISNULL(g.visits,CONVERT(bigint,0)) AS visits,
        ISNULL(g.patients,CONVERT(bigint,0)) AS patients,
        CONVERT(decimal(28,6),ISNULL(g.revenue,0)) AS revenue
    FROM categories c LEFT JOIN split_totals g ON g.category=c.category
)
SELECT s.staff_id,s.staff_name,c.category,c.visits,c.patients,c.revenue,
    v.visits AS total_visits,v.patients AS total_unique_patients,
    CONVERT(decimal(28,6),f.revenue) AS revenue_total,
    CONVERT(decimal(28,6),v.linked_revenue) AS revenue_linked_completed,
    t.visits-v.visits AS unclassified_visits,f.service_rows,
    f.null_count AS null_amount_count,f.missing_order_count
FROM target_staff s CROSS JOIN combined c CROSS JOIN visit_totals v
CROSS JOIN target_counts t CROSS JOIN rendered_totals f ORDER BY c.category
