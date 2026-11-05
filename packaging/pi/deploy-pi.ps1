<#
.SYNOPSIS
    Déploiement à distance du VDS Player sur Raspberry Pi via SSH / SCP.

.DESCRIPTION
    Copie l'ensemble du dossier packaging/pi sur le Pi et exécute automatiquement install.sh en sudo.

.EXAMPLE
    .\deploy-pi.ps1 -PiHost 192.168.1.50 -User pi -ServerUrl "https://allia-innovation.fr/espace/affichage"
#>

[CmdletBinding()]
param (
    [Parameter(Mandatory = $true, HelpMessage = "Adresse IP ou nom d'hôte du Raspberry Pi")]
    [string]$PiHost,

    [Parameter(Mandatory = $false)]
    [string]$User = "pi",

    [Parameter(Mandatory = $false)]
    [string]$ServerUrl = "https://allia-innovation.fr/espace/affichage"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Déploiement distant VDS Player vers Raspberry Pi ($User@$PiHost) ===" -ForegroundColor Cyan

# Vérification SSH
if (-not (Get-Command ssh -ErrorAction SilentlyContinue) -or -not (Get-Command scp -ErrorAction SilentlyContinue)) {
    Write-Error "Erreur : 'ssh' ou 'scp' introuvable dans le PATH."
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. Copie des fichiers d'installation vers /tmp/vds-pi sur le Pi
Write-Host "-> Transfert des fichiers d'installation vers le Pi..." -ForegroundColor Yellow
ssh "$User@$PiHost" "mkdir -p /tmp/vds-pi"
scp -r "$ScriptDir\*" "$User@$PiHost:/tmp/vds-pi/"

# 2. Exécution de install.sh sur le Pi
Write-Host "-> Exécution de install.sh en root sur le Pi..." -ForegroundColor Yellow
ssh -t "$User@$PiHost" "chmod +x /tmp/vds-pi/install.sh && sudo /tmp/vds-pi/install.sh '$ServerUrl'"

Write-Host "=== Déploiement distant terminé ===" -ForegroundColor Green
