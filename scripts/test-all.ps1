$ErrorActionPreference = "Stop"

conda run --no-capture-output -n mycoder python -m pytest
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

npm --workspace '@jixue/desktop' run test
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
