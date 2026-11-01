param(
    [string]$JavaHome = "$PSScriptRoot\..\..\.cache\jdk-copy",
    [string]$AndroidSdk = "D:\Dev\android-sdk",
    [string]$BuildToolsVersion = "35.0.0"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$out = Join-Path $repo ".cache\tls-relay"
New-Item -ItemType Directory -Force "$out\classes", "$out\dex" | Out-Null
$javaSources = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.java' | ForEach-Object FullName)
& "$JavaHome\bin\javac.exe" -source 8 -target 8 -cp "$AndroidSdk\platforms\android-35\android.jar" -d "$out\classes" @javaSources
if ($LASTEXITCODE) { throw "Relay Java compilation failed" }
$previousJavaHome = $env:JAVA_HOME
try {
    $env:JAVA_HOME = $JavaHome
    $classFiles = @(Get-ChildItem -LiteralPath "$out\classes" -Filter '*.class' | ForEach-Object FullName)
    & "$AndroidSdk\build-tools\$BuildToolsVersion\d8.bat" --lib "$AndroidSdk\platforms\android-35\android.jar" --output "$out\dex" @classFiles
    if ($LASTEXITCODE) { throw "Relay dex compilation failed" }
} finally {
    $env:JAVA_HOME = $previousJavaHome
}
& "$JavaHome\bin\jar.exe" cf "$repo\vendor\inputsharex-relay.jar" -C "$out\dex" classes.dex
if ($LASTEXITCODE) { throw "Relay jar packaging failed" }
