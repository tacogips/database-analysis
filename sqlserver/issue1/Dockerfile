FROM mcr.microsoft.com/dotnet/sdk:10.0@sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
WORKDIR /src
COPY . ./
# Owner-reviewed direct and transitive versions are committed; any graph drift fails.
RUN dotnet restore --locked-mode
RUN dotnet build -c Release --no-restore -o /app
RUN mkdir /evidence && cp packages.lock.json /evidence/packages.lock.json && dotnet --info > /evidence/dotnet-info.txt
WORKDIR /app
ENTRYPOINT ["dotnet", "CountIssue1.dll"]
