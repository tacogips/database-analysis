FROM mysql:8.4.13@sha256:a81d043930e51745522bd5c430961cf8286f0a23f0a9a2a6e0108f7ea71b0902
RUN microdnf install -y python3 python3-pip perl-DBD-MySQL 2>/dev/null || microdnf install -y python3 python3-pip
RUN pip3 install --no-cache-dir pymysql==1.1.1 cryptography==43.0.3
ENTRYPOINT []
