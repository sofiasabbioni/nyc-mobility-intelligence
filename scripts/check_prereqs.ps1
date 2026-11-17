# check_prereps.ps1 - safe read-only environment checks
$commands = @("git", "python", "docker")
foreach ($name in $commands) {
    $item = Get-Command $name -ErrorAction Silentlycontinue
    if ($null -eq $item) {
        Write-Host "MISSING: $name" -ForegroundColor Yellow
    } else {
        Write-Host "FOUND: $name at $($item.Source)"
    }
}

Write-Host "`nRepository folder checks:"
foreach ($name in @("docker-compose.yml", ".env.example", "requirements-dev.txt", "app", "loader", "benchmark", "tests")) {
    if (Test-Path $name) {
        Write-Host "OK: $name"
    } else {
        Write-Host "MISSING: $name" - ForegroundColor Yellow
    }
}

if (Get-Command docker -ErrorAction Silentlycontinue) {
    Write-Host "`nDocker Compose version:"
    docker compose version
    Write-Host "`nChecking Compose syntax (without starting containers):"
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Compose validation failed" - ForegroundColor Red
    }
}

Write-Host "`nThese checks do NOT prove that database loading or the dashboard ran successfully"