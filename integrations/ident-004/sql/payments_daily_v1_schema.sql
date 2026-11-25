SELECT
    c.name AS column_name,
    TYPE_NAME(c.system_type_id) AS data_type,
    c.is_nullable,
    c.precision,
    c.scale
FROM sys.columns AS c
WHERE c.object_id = OBJECT_ID(N'dbo.PaymentsIn', N'U')
  AND c.name IN (N'DatePayment', N'ID_Patients', N'Payment')
ORDER BY c.name;
