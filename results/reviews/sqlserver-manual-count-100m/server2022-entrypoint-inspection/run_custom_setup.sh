#!/bin/bash
#
# This script executes the custom setup phase that runs after the SQL Server process has started.
#

SQLCMD=/opt/mssql-tools18/bin/sqlcmd
if [ ! -x $SQLCMD ]; then
    SQLCMD=/opt/mssql-tools/bin/sqlcmd
	if [ ! -x $SQLCMD ]; then
		echo "sqlcmd not available at $SQLCMD, unable to execute custom setup."
		exit 1
	fi
fi

sqlcmd_sa() {
	SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" "$SQLCMD" -C -S 127.0.0.1 -U SA "$@"
}

function IsSqlServerReady
{
    IS_SERVER_READY_QUERY='SET NOCOUNT ON; Select SUM(state) from sys.databases'
    dbStatus=$(sqlcmd_sa -h -1 -Q "$IS_SERVER_READY_QUERY" 2>/dev/null)
    errCode=$?
    if [[ "$errCode" -eq "0" && "$dbStatus" -eq "0" ]]; then
        return 0
    else
        return 1
    fi
}

if [ -z "$MSSQL_CUSTOM_INIT" ]; then
    exit 0
fi

echo "Waiting for Sql Server to be ready before executing custom setup"
until IsSqlServerReady; do 
    sleep 5;
done

# Escape a value for use inside a T-SQL bracket-delimited identifier ([...]).
# Doubles ] to ]] (same as QUOTENAME), rejects newlines, carriage returns,
# and names longer than 128 characters (sysname limit).
escape_tsql_bracket_identifier() {
    local ident="$1"
    if [[ -z "$ident" ]]; then
        echo "Error: identifier is empty." >&2
        exit 1
    fi
    if [[ -z "${ident//[[:space:]]/}" ]]; then
        echo "Error: identifier contains only whitespace." >&2
        exit 1
    fi
    if [[ ${#ident} -gt 128 ]]; then
        echo "Error: identifier exceeds 128 characters." >&2
        exit 1
    fi
    if [[ "$ident" == *$'\n'* ]]; then
        echo "Error: identifier contains newline characters." >&2
        exit 1
    fi
    if [[ "$ident" == *$'\r'* ]]; then
        echo "Error: identifier contains carriage return characters." >&2
        exit 1
    fi
    printf '%s' "${ident//]/]]}"
}

if [ -n "$MSSQL_DB" ]; then
    db_ident="$(escape_tsql_bracket_identifier "$MSSQL_DB")"
    if [[ $? -ne 0 ]]; then
        echo "Error: failed to read MSSQL_DB." >&2
        exit 1
    fi

    echo "Creating database $MSSQL_DB"
    sqlcmd_sa -Q "CREATE DATABASE [$db_ident]"
    
    if [[ -n $MSSQL_USER && "$MSSQL_USER" != "sa" ]]; then
        user_ident="$(escape_tsql_bracket_identifier "$MSSQL_USER")"
        if [[ $? -ne 0 ]]; then
            echo "Error: failed to read MSSQL_USER." >&2
            exit 1
        fi
        echo "Creating login $MSSQL_USER with password defined in MSSQL_PASSWORD environment variable"

        # Escape single quotes in password for T-SQL string literal
        escaped_pw="${MSSQL_PASSWORD//\'/\'\'}"
        printf "CREATE LOGIN [%s] WITH PASSWORD = '%s';\nUSE [%s];\nCREATE USER [%s] FROM LOGIN [%s];\nGRANT CONTROL to [%s];\n" \
            "$user_ident" "$escaped_pw" "$db_ident" "$user_ident" "$user_ident" "$user_ident" \
            | sqlcmd_sa
    fi
fi

if [ -n "${MSSQL_SETUP_SCRIPTS_LOCATION}" ]; then
    for file in "$MSSQL_SETUP_SCRIPTS_LOCATION"/*; do
        echo "Executing custom setup script $file"
        sqlcmd_sa -i "$file"
    done
fi
