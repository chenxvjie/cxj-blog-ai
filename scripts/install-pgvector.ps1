$ErrorActionPreference = 'Stop'
$sourceDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../.local/pgvector'))
$postgresRoot = 'E:\PostgreSQL\18'
if (!(Test-Path -LiteralPath (Join-Path $sourceDirectory 'vector.dll'))) { throw 'Compiled pgvector DLL is missing' }
Copy-Item -LiteralPath (Join-Path $sourceDirectory 'vector.dll') -Destination (Join-Path $postgresRoot 'lib/vector.dll')
Copy-Item -LiteralPath (Join-Path $sourceDirectory 'vector.control') -Destination (Join-Path $postgresRoot 'share/extension/vector.control')
Get-ChildItem -LiteralPath (Join-Path $sourceDirectory 'sql') -Filter 'vector--*.sql' | Copy-Item -Destination (Join-Path $postgresRoot 'share/extension')
Write-Output 'pgvector installed successfully. No PostgreSQL restart required for first installation.'
