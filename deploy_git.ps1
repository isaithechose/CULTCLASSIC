# Deploy basado en git: commit + push local y actualizacion del VPS en un solo paso.
#
# Uso:
#   .\deploy_git.ps1 -Message "fix: lo que cambiaste"
#
# Requisitos:
#   - Estar en la carpeta del proyecto (donde esta manage.py).
#   - Tener la llave SSH configurada para el VPS (la misma que usa deploy.ps1).

param(
    [Parameter(Mandatory = $true)][string]$Message,
    [string]$HostName = "root@187.124.250.115",
    [string]$RemoteRoot = "/var/www/CULTCLASSIC",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"

function Run($cmd) {
    Write-Host ">> $cmd" -ForegroundColor Cyan
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) { throw "Fallo el comando: $cmd" }
}

# 1. Verificacion local antes de subir nada
Run "venv\Scripts\python.exe manage.py check"

# 2. Commit local (tolera 'no hay cambios') y push a la rama de produccion
Run "git add -A"
Write-Host ">> git commit -m `"$Message`"" -ForegroundColor Cyan
git commit -m "$Message"   # exit 1 si no hay nada que commitear: se ignora a proposito
Run "git push origin HEAD:$Branch"

# 3. Deploy en el VPS: dejar el servidor identico al remoto y reiniciar
$remote = @(
    "cd $RemoteRoot",
    "git fetch origin $Branch",
    "git reset --hard origin/$Branch",
    "source venv/bin/activate",
    "pip install -r requirements.txt",
    "python manage.py check",
    "python manage.py collectstatic --noinput",
    "systemctl restart cultclasiccs nginx",
    "systemctl is-active cultclasiccs nginx"
) -join " && "

Run "ssh $HostName `"$remote`""

Write-Host "Deploy completo." -ForegroundColor Green
