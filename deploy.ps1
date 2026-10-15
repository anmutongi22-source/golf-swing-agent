<#
.SYNOPSIS
  Deploy Golf Swing Coach to Azure App Service (Linux, Python). No Docker needed:
  the source is zipped and Azure installs the packages during deployment.

.EXAMPLE
  .\deploy.ps1 -AppName golf-swing-aidan

.NOTES
  AppName must be unique across all of Azure; it becomes https://<AppName>.azurewebsites.net
  Safe to re-run: existing resources are reused and the code is redeployed.
#>
param(
    [Parameter(Mandatory = $true)][string]$AppName,
    [string]$ResourceGroup   = "rg-golf-swing-coach",
    [string]$Location        = "eastus",
    [string]$Sku             = "B1",
    [string]$Tenant          = "d523cc7a-4233-4898-9124-b9237e9f7717",
    [string]$Subscription    = "",
    [ValidateSet("foundry", "anthropic")][string]$Provider = "foundry",
    [string]$FoundryResource = "aif-agentopsplat-dev",
    [string]$ModelId         = "claude-haiku-4-5"
)

# az writes progress to stderr; 'Stop' would turn that into errors in Windows PowerShell.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$PlanName = "$AppName-plan"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nFAILED: $text" -ForegroundColor Red; exit 1 }

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Fail "Azure CLI not found. Install it with:  winget install --exact --id Microsoft.AzureCLI  (then reopen PowerShell)"
}

# --- 1. Sign in -------------------------------------------------------------
Step "Checking Azure sign-in"
$account = az account show -o json 2>$null | ConvertFrom-Json
if (-not $account -or $account.tenantId -ne $Tenant) {
    az login --tenant $Tenant -o none
    if ($LASTEXITCODE -ne 0) { Fail "Sign-in failed. Try: az login --tenant $Tenant --use-device-code" }
}
if ($Subscription) {
    az account set --subscription $Subscription
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't switch to subscription '$Subscription'." }
}
$account = az account show -o json | ConvertFrom-Json
Write-Host "Using subscription: $($account.name)"

# --- 2. Model key -----------------------------------------------------------
Step "Getting the model API key"
$apiKey = ""
if ($Provider -eq "foundry") {
    $foundryRg = az cognitiveservices account list --query "[?name=='$FoundryResource'].resourceGroup | [0]" -o tsv 2>$null
    if ($foundryRg) {
        $apiKey = az cognitiveservices account keys list --name $FoundryResource --resource-group $foundryRg --query key1 -o tsv 2>$null
    }
    if ($apiKey) { Write-Host "Found the key for $FoundryResource automatically." }
    else { Write-Host "Couldn't read the key for $FoundryResource (it may be in another subscription, or you lack access)." }
}
if (-not $apiKey) {
    $label = if ($Provider -eq "foundry") { "Foundry API key for $FoundryResource" } else { "Anthropic API key (sk-ant-...)" }
    $secure = Read-Host "Paste your $label" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if (-not $apiKey) { Fail "No API key provided." }

# --- 3. Resources -----------------------------------------------------------
Step "Creating resource group $ResourceGroup"
az group create --name $ResourceGroup --location $Location -o none
if ($LASTEXITCODE -ne 0) { Fail "Couldn't create the resource group. You may not have permission in this subscription." }

Step "Creating App Service plan $PlanName ($Sku, Linux)"
az appservice plan create --name $PlanName --resource-group $ResourceGroup --location $Location --sku $Sku --is-linux -o none
if ($LASTEXITCODE -ne 0) { Fail "Couldn't create the App Service plan." }

Step "Creating web app $AppName"
$existing = az webapp show --name $AppName --resource-group $ResourceGroup --query name -o tsv 2>$null
if (-not $existing) {
    az webapp create --name $AppName --resource-group $ResourceGroup --plan $PlanName --runtime "PYTHON:3.12" -o none
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't create the web app. The name '$AppName' may already be taken; try another." }
} else {
    Write-Host "Web app already exists; reusing it."
}

# --- 4. Configuration -------------------------------------------------------
Step "Applying settings"
$settings = @(
    "SCM_DO_BUILD_DURING_DEPLOYMENT=true",
    "MODEL_PROVIDER=$Provider",
    "MODEL_ID=$ModelId"
)
if ($Provider -eq "foundry") {
    $settings += "FOUNDRY_RESOURCE=$FoundryResource"
    $settings += "FOUNDRY_API_KEY=$apiKey"
} else {
    $settings += "ANTHROPIC_API_KEY=$apiKey"
}
az webapp config appsettings set --name $AppName --resource-group $ResourceGroup --settings @settings -o none
if ($LASTEXITCODE -ne 0) { Fail "Couldn't save app settings." }

$startup = "gunicorn --bind=0.0.0.0:8000 --timeout 300 --workers 2 -k uvicorn.workers.UvicornWorker app.main:app"
az webapp config set --name $AppName --resource-group $ResourceGroup --startup-file $startup -o none
if ($LASTEXITCODE -ne 0) { Fail "Couldn't set the startup command." }

az webapp log config --name $AppName --resource-group $ResourceGroup --docker-container-logging filesystem -o none

# --- 5. Package and deploy --------------------------------------------------
Step "Packaging code"
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zipPath = Join-Path $env:TEMP "golf-swing-coach-deploy.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, "Create")
try {
    $files = @(Get-Item (Join-Path $PSScriptRoot "requirements.txt")) +
             @(Get-ChildItem (Join-Path $PSScriptRoot "app") -Recurse -File |
               Where-Object { $_.FullName -notmatch "__pycache__" })
    foreach ($f in $files) {
        # Forward slashes so the zip unpacks correctly on Linux.
        $entry = $f.FullName.Substring($PSScriptRoot.Length).TrimStart("\", "/") -replace "\\", "/"
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $entry)
    }
} finally { $zip.Dispose() }
Write-Host "Packaged $($files.Count) files."

Step "Deploying (Azure installs the Python packages; this takes 3-6 minutes the first time)"
az webapp deploy --name $AppName --resource-group $ResourceGroup --src-path $zipPath --type zip -o none
if ($LASTEXITCODE -ne 0) {
    Fail "Deployment failed. See the build log with:  az webapp log deployment show --name $AppName --resource-group $ResourceGroup"
}
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

# --- 6. Wait for readiness --------------------------------------------------
$hostName = az webapp show --name $AppName --resource-group $ResourceGroup --query defaultHostName -o tsv
$base = "https://$hostName"
Step "Waiting for $base/management/health/readiness"
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $r = Invoke-RestMethod "$base/management/health/readiness" -TimeoutSec 20
        if ($r.status -eq "ok") { $ready = $true; break }
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        Write-Host "  attempt $i/30: $(if ($code) { "HTTP $code" } else { 'starting' })"
    }
    Start-Sleep -Seconds 10
}

if ($ready) {
    Write-Host "`nReady: status ok (503 -> 200)." -ForegroundColor Green
    Write-Host "Open the app:  $base" -ForegroundColor Green
} else {
    Write-Host "`nThe app didn't report ready within 5 minutes." -ForegroundColor Yellow
    Write-Host "Watch the live logs with:  az webapp log tail --name $AppName --resource-group $ResourceGroup"
}
