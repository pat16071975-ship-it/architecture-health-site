SELECT N'identity' AS check_name, CONVERT(int, CASE WHEN
    SUSER_SNAME() = ORIGINAL_LOGIN() AND USER_NAME() NOT IN (N'dbo', N'guest', N'sys', N'INFORMATION_SCHEMA')
    AND IS_SRVROLEMEMBER(N'sysadmin') = 0
    AND EXISTS (SELECT 1 FROM sys.login_token WHERE type = N'SQL LOGIN' AND name = ORIGINAL_LOGIN() AND usage = N'GRANT OR DENY')
    AND EXISTS (SELECT 1 FROM sys.user_token WHERE type = N'SQL USER' AND name = USER_NAME() AND usage = N'GRANT OR DENY')
    THEN 1 ELSE 0 END) AS passed
UNION ALL
SELECT N'roles', CASE WHEN
    NOT EXISTS (SELECT 1 FROM sys.login_token WHERE usage <> N'AUTHENTICATOR' AND NOT (type = N'SQL LOGIN' AND name = ORIGINAL_LOGIN() OR type = N'SERVER ROLE' AND name = N'public'))
    AND NOT EXISTS (SELECT 1 FROM sys.user_token WHERE usage <> N'AUTHENTICATOR' AND NOT (type = N'SQL USER' AND name = USER_NAME() OR type IN (N'ROLE', N'DATABASE ROLE') AND name IN (N'public', N'db_datareader')))
    THEN 1 ELSE 0 END
UNION ALL
SELECT N'server_effective', CASE WHEN
    EXISTS (SELECT 1 FROM sys.fn_my_permissions(NULL, N'SERVER') WHERE permission_name = N'CONNECT SQL')
    AND NOT EXISTS (SELECT 1 FROM sys.fn_my_permissions(NULL, N'SERVER') WHERE permission_name NOT IN (N'CONNECT SQL', N'VIEW ANY DATABASE', N'VIEW ANY DEFINITION'))
    THEN 1 ELSE 0 END
UNION ALL
SELECT N'database_effective', CASE WHEN
    EXISTS (SELECT 1 FROM sys.fn_my_permissions(NULL, N'DATABASE') WHERE permission_name = N'CONNECT')
    AND NOT EXISTS (SELECT 1 FROM sys.fn_my_permissions(NULL, N'DATABASE') WHERE permission_name NOT IN (N'CONNECT', N'SELECT', N'VIEW DEFINITION', N'VIEW ANY COLUMN MASTER KEY DEFINITION', N'VIEW ANY COLUMN ENCRYPTION KEY DEFINITION'))
    THEN 1 ELSE 0 END
UNION ALL
SELECT N'server_grants', CASE WHEN NOT EXISTS (
    SELECT 1 FROM sys.server_permissions AS p
    JOIN sys.login_token AS t ON t.principal_id = p.grantee_principal_id
    WHERE t.usage = N'GRANT OR DENY' AND p.state IN (N'G', N'W')
      AND (p.state = N'W' OR p.permission_name NOT IN (N'CONNECT', N'CONNECT SQL', N'VIEW ANY DATABASE', N'VIEW ANY DEFINITION', N'VIEW DEFINITION'))
    ) THEN 1 ELSE 0 END
UNION ALL
SELECT N'database_grants', CASE WHEN NOT EXISTS (
    SELECT 1 FROM sys.database_permissions AS p
    JOIN sys.user_token AS t ON t.principal_id = p.grantee_principal_id
    WHERE t.usage = N'GRANT OR DENY' AND p.state IN (N'G', N'W')
      AND (p.state = N'W' OR p.permission_name NOT IN (N'CONNECT', N'SELECT', N'VIEW DEFINITION', N'VIEW ANY COLUMN MASTER KEY DEFINITION', N'VIEW ANY COLUMN ENCRYPTION KEY DEFINITION'))
    ) THEN 1 ELSE 0 END
UNION ALL
SELECT N'ownership', CASE WHEN
    NOT EXISTS (SELECT 1 FROM sys.schemas AS s JOIN sys.user_token AS t ON t.principal_id = s.principal_id WHERE t.usage = N'GRANT OR DENY')
    AND NOT EXISTS (SELECT 1 FROM sys.objects AS o JOIN sys.user_token AS t ON t.principal_id = o.principal_id WHERE t.usage = N'GRANT OR DENY')
    AND NOT EXISTS (SELECT 1 FROM sys.database_principals AS p JOIN sys.user_token AS t ON t.principal_id = p.owning_principal_id WHERE t.usage = N'GRANT OR DENY')
    AND NOT EXISTS (SELECT 1 FROM sys.server_principals AS p JOIN sys.login_token AS t ON t.principal_id = p.owning_principal_id WHERE t.usage = N'GRANT OR DENY')
    THEN 1 ELSE 0 END
UNION ALL
SELECT N'payments_effective', CASE WHEN
    HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'SELECT') = 1
    AND HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'SELECT', N'DatePayment', N'COLUMN') = 1
    AND HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'SELECT', N'ID_Patients', N'COLUMN') = 1
    AND HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'SELECT', N'Payment', N'COLUMN') = 1
    AND HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'INSERT') = 0
    AND HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'UPDATE') = 0
    AND HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'DELETE') = 0
    AND HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'ALTER') = 0
    AND HAS_PERMS_BY_NAME(N'dbo.PaymentsIn', N'OBJECT', N'CONTROL') = 0
    AND NOT EXISTS (SELECT 1 FROM sys.fn_my_permissions(N'dbo.PaymentsIn', N'OBJECT') WHERE permission_name NOT IN (N'SELECT', N'VIEW DEFINITION'))
    THEN 1 ELSE 0 END;
