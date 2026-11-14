FROM --platform=linux/arm64 mcr.microsoft.com/dotnet/sdk:10.0@sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
WORKDIR /src
COPY . ./
RUN mkdir /provenance && dotnet --info > /provenance/dotnet-info.txt && cp packages.lock.json /provenance/packages.lock.json
RUN dotnet restore --locked-mode > /provenance/restore.log 2>&1 || (cat /provenance/restore.log; exit 1)
RUN dotnet build -c Release --no-restore -o /app > /provenance/build.log 2>&1 || (cat /provenance/build.log; exit 1)
RUN cp -r sql /app/sql
WORKDIR /app
ENTRYPOINT ["dotnet", "ManualCount100m.dll"]
