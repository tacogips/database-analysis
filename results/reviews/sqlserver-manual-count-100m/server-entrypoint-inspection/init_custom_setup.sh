#!/bin/bash
#
# This script prepares the custom setup phase that will run after 
# the SQL Server process has started.
#
MSSQL_SETUP_SCRIPTS_LOCATION="/mssql-server-setup-scripts.d"

function CheckIfDataDirectoryIsEmpty
{
    local dir="/var/opt/mssql/data"
    if [ ! -d "${dir}" ]; then
        true
    elif [ -z "$(ls -A ${dir})" ]; then
        true
    else
        false
    fi
}

if [ -n "${MSSQL_DB}" ]; then
    if ! CheckIfDataDirectoryIsEmpty; then
        echo "Creation of db $MSSQL_DB was requested, but the data directory is not empty, ignoring."
    else
        if [[ -z "${MSSQL_USER}" && -z "${MSSQL_PASSWORD}" ]] ||
           [[ -n "${MSSQL_USER}" && -n "${MSSQL_PASSWORD}" ]]; then
            export MSSQL_CUSTOM_INIT=true
        else
            echo "Either both MSSQL_USER and MSSQL_PASSWORD must be set together, or none of them."
            exit 1
        fi
    fi
fi

if [ -d "${MSSQL_SETUP_SCRIPTS_LOCATION}" ]; then
    if ! CheckIfDataDirectoryIsEmpty; then
        echo "Custom setup script provided, but the data directory is not empty, ignoring."
    else
        export MSSQL_CUSTOM_INIT=true
        export MSSQL_SETUP_SCRIPTS_LOCATION
    fi
fi
