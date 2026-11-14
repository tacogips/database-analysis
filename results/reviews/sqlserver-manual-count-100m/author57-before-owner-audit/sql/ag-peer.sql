CREATE LOGIN peer_endpoint_login WITH PASSWORD='ManualCount100m_Peer_2026!';
CREATE USER peer_endpoint_user FOR LOGIN peer_endpoint_login;
CREATE CERTIFICATE peer_endpoint_cert AUTHORIZATION peer_endpoint_user FROM FILE='/backup/__PEER__.cer';
GRANT CONNECT ON ENDPOINT::Hadr_endpoint TO peer_endpoint_login;
