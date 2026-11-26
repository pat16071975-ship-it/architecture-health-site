SELECT
    CONVERT(char(10), DatePayment, 23) AS payment_date,
    COUNT_BIG(*) AS records_count,
    COUNT_BIG(DISTINCT CASE WHEN Payment > 0 THEN ID_Patients END) AS paying_patients,
    CONVERT(varchar(80), SUM(CASE WHEN Payment > 0 THEN Payment ELSE 0 END)) AS positive_amount,
    CONVERT(varchar(80), SUM(CASE WHEN Payment < 0 THEN Payment ELSE 0 END)) AS negative_amount,
    CONVERT(varchar(80), SUM(Payment)) AS net_amount
FROM dbo.PaymentsIn
WHERE DatePayment >= @start_date AND DatePayment < @end_date_exclusive
GROUP BY DatePayment
ORDER BY DatePayment;
