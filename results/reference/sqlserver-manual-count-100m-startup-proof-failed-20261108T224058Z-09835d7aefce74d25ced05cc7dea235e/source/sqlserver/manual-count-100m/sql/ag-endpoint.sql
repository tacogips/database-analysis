-- Public disposable fixture password. Private keys remain in owned master DB, never exported.
CREATE MASTER KEY ENCRYPTION BY PASSWORD='ManualCount100m_MasterKey_2026!';
CREATE CERTIFICATE local_endpoint_cert WITH SUBJECT='ManualCount100m __NODE__ endpoint';
BACKUP CERTIFICATE local_endpoint_cert TO FILE='/backup/__NODE__.cer';
CREATE ENDPOINT Hadr_endpoint STATE=STARTED AS TCP(LISTENER_PORT=5022)
 FOR DATABASE_MIRRORING(ROLE=ALL,AUTHENTICATION=CERTIFICATE local_endpoint_cert,ENCRYPTION=REQUIRED ALGORITHM AES);
