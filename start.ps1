$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not $env:MATERIAL_PORT) { $env:MATERIAL_PORT = '8765' }
if (-not $env:MATERIAL_LLM_BASE_URL) { $env:MATERIAL_LLM_BASE_URL = 'https://api.openai.com/v1' }
if (-not $env:MATERIAL_LLM_MODEL) { $env:MATERIAL_LLM_MODEL = 'gpt-4.1' }

Write-Host 'Material Writing System'
Write-Host "Work dir: $Root"
Write-Host "URL: http://127.0.0.1:$env:MATERIAL_PORT"
Write-Host "Model endpoint: $env:MATERIAL_LLM_BASE_URL"
Write-Host "Model: $env:MATERIAL_LLM_MODEL"
Write-Host ''

if (-not $env:MATERIAL_LLM_API_KEY) {
  $secure = Read-Host 'Paste API key for this session only (Enter to skip)' -AsSecureString
  if ($secure.Length -gt 0) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:MATERIAL_LLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
  }
}

Write-Host 'Starting local server...'
Write-Host 'Open this address in your browser:'
Write-Host "http://127.0.0.1:$env:MATERIAL_PORT"
python .\backend\server.py
