SELECT CONVERT(nvarchar(128), SERVERPROPERTY('ProductVersion')) AS sql_version,
       d.compatibility_level, d.is_read_committed_snapshot_on,
       d.snapshot_isolation_state_desc
FROM sys.databases AS d
WHERE d.database_id = DB_ID();

SELECT HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'SELECT') AS can_select,
       HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'INSERT') AS can_insert,
       HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'UPDATE') AS can_update,
       HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'DELETE') AS can_delete,
       HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'CREATE TABLE') AS can_create_table;

SELECT i.name AS index_name, i.type_desc, i.is_disabled, i.has_filter,
       ic.key_ordinal, ic.is_included_column, c.name AS column_name
FROM sys.indexes AS i
JOIN sys.index_columns AS ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
JOIN sys.columns AS c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
WHERE i.object_id = OBJECT_ID(N'dbo.PaymentsIn', N'U')
ORDER BY i.index_id, ic.key_ordinal, ic.index_column_id;
