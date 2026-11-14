#!/bin/bash

PERMISSIONS_CHECK_PATH="/opt/mssql/bin/permissions_check.sh"

if [ -x $PERMISSIONS_CHECK_PATH ]; then
    $PERMISSIONS_CHECK_PATH
else
    # This should never happen. A permissions_check.sh script has to exist if a
    # Dockerfile uses launch_sqlservr.sh as ENTRYPOINT
    #
    echo "Unable to run startup permissions check, exiting."
    exit 1
fi

case "${MSSQL_PID,,}" in
    standarddeveloper) 
        export MSSQL_PID="DeveloperStandard"
        ;;
    enterprisedeveloper) 
        export MSSQL_PID="Developer"
        ;;
esac

source /opt/mssql/bin/init_custom_setup.sh
if [ $? -ne 0 ]; then
    exit 1
fi

"$@" &
pid="$!"

/opt/mssql/bin/run_custom_setup.sh
if [ $? -ne 0 ]; then
    exit 1
fi

wait $pid
