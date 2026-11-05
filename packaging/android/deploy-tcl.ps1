<#
.SYNOPSIS
    Déploiement et enrôlement automatisé du VDS Player sur TCL Google TV (Android TV).

.DESCRIPTION
    Ce script :
    1. Se connecte en ADB réseau sur la TV.
    2. Vérifie ou génère l'APK (assembleDebug).
    3. Installe l'APK.
    4. Configure le kiosque (Device Owner ou Kiosque doux).
    5. Provisionne l'URL du serveur VDS et désactive la mise en veille d'inactivité.

.EXAMPLE
    .\deploy-tcl.ps1 -TvIp 192.168.1.100 -ServerUrl "https://allia-innovation.fr/espace/affichage" -DeviceOwner
#>

[CmdletBinding()]
param (
    [Parameter(Mandatory = $true, HelpMessage = "Adresse IP de la TV TCL (ex: 192.168.1.50)")]
    [string]$TvIp,

    [Parameter(Mandatory = $false)]
    [string]$ServerUrl = "https://allia-innovation.fr/espace/affichage",

    [Parameter(Mandatory = $false)]
    [switch]$DeviceOwner,

    [Parameter(Mandatory = $false)]
    [switch]$BuildFirst,

    [Parameter(Mandatory = $false)]
    [string]$PairingTicket = ""
)

$ErrorActionPreference = "Stop"

Write-Host "=== Déploiement VDS Player sur Android TV ($TvIp) ===" -ForegroundColor Cyan

# 1. Vérification ADB
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    Write-Error "Erreur : 'adb' introuvable dans le PATH. Installez Android SDK Platform-Tools."
}

# 2. Vérification / Build de l'APK
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApkPath = Join-Path $ScriptDir "app\build\outputs\apk\debug\app-debug.apk"

if ($BuildFirst -or (-not (Test-Path $ApkPath))) {
    Write-Host "-> Compilation de l'APK debug..." -ForegroundColor Yellow
    Push-Location $ScriptDir
    try {
        if ($IsWindows -or ($env:OS -like "*Windows*")) {
            & ".\gradlew.bat" ":app:assembleDebug"
        } else {
            & "./gradlew" ":app:assembleDebug"
        }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $ApkPath)) {
        Write-Error "Échec de compilation : APK introuvable à $ApkPath"
    }
}

Write-Host "-> APK prête : $ApkPath" -ForegroundColor Green

# 3. Connexion ADB
Write-Host "-> Connexion ADB à $TvIp:5555..." -ForegroundColor Yellow
adb connect "$($TvIp):5555"

# Vérifier que l'appareil est connecté
$devices = adb devices
if ($devices -notmatch "$($TvIp):5555\s+device") {
    Write-Error "Impossible de joindre la TV en ADB. Assurez-vous que le débogage réseau est activé dans Options pour les développeurs et que vous avez accepté l'empreinte sur la TV."
}
Write-Host "-> TV connectée avec succès." -ForegroundColor Green

# 4. Installation de l'APK
Write-Host "-> Installation de l'application..." -ForegroundColor Yellow
adb install -r $ApkPath

# 5. Gestion du mode Kiosque
if ($DeviceOwner) {
    Write-Host "-> Vérification de l'éligibilité Device Owner..." -ForegroundColor Yellow
    $accounts = adb shell dumpsys account
    if ($accounts -match "Account \{") {
        Write-Warning "Des comptes Google ou constructeur sont présents sur la TV."
        Write-Warning "Pour 'dpm set-device-owner', aucun compte ne doit exister."
        Write-Warning "Retirez temporairement le compte dans Paramètres > Comptes, puis relancez le script."
        Write-Host "Bascule en mode Kiosque doux (sans Device Owner)..." -ForegroundColor Yellow
        $DeviceOwner = $false
    } else {
        Write-Host "-> Activation de Device Owner..." -ForegroundColor Yellow
        adb shell dpm set-device-owner "app.vds.player/.AdminReceiver"
        Write-Host "-> Device Owner activé avec succès !" -ForegroundColor Green
    }
}

if (-not $DeviceOwner) {
    Write-Host "-> Configuration du Kiosque doux..." -ForegroundColor Yellow
    adb shell cmd package set-home-activity "app.vds.player/.MainActivity"
    adb shell appops set app.vds.player SYSTEM_ALERT_WINDOW allow
}

# 6. Réglages écran & veille
Write-Host "-> Désactivation de la mise en veille automatique Android..." -ForegroundColor Yellow
adb shell settings put secure attentive_timeout -1

# 7. Provisioning de l'URL serveur
Write-Host "-> Provisioning de l'URL serveur : $ServerUrl" -ForegroundColor Yellow
$amArgs = @("shell", "am", "start", "-n", "app.vds.player/.ProvisionActivity", "--es", "server_url", $ServerUrl)
if ($PairingTicket) {
    $amArgs += @("--es", "pairing_ticket", $PairingTicket)
}
& adb @amArgs

# 8. Lancement
Write-Host "-> Lancement du Player VDS..." -ForegroundColor Yellow
adb shell am start -n "app.vds.player/.MainActivity"

Write-Host "=== Déploiement terminé avec succès sur $TvIp ===" -ForegroundColor Green
Write-Host "Le player doit afficher l'écran d'appairage ou la diffusion active."
