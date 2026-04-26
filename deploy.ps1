param(
    [string]$HostName = "root@187.124.250.115",
    [string]$RemoteRoot = "/var/www/CULTCLASSIC",
    [int]$ProductId = 571
)

$ErrorActionPreference = "Stop"

function Run($Command) {
    Write-Host ">> $Command" -ForegroundColor Cyan
    Invoke-Expression $Command
}

Run "venv\Scripts\python.exe manage.py check"
Run "venv\Scripts\python.exe manage.py test tienda"
Run "venv\Scripts\python.exe manage.py optimize_product_images --pattern `"OversizedTee_*.webp`""
Run "venv\Scripts\python.exe manage.py assign_variant_images --product-id $ProductId"
Run "venv\Scripts\python.exe manage.py validate_variant_images --product-id $ProductId"

Run "ssh $HostName `"mkdir -p $RemoteRoot/tienda/management/commands $RemoteRoot/CULTCALLE/media/productos`""

Run "scp templates/tienda/detalle_producto.html tienda/admin.py tienda/utils/variant_image_assignment.py tienda/management/__init__.py tienda/management/commands/__init__.py tienda/management/commands/assign_variant_images.py tienda/management/commands/optimize_product_images.py tienda/management/commands/validate_variant_images.py ${HostName}:/tmp/"

Run "ssh $HostName `"mv /tmp/detalle_producto.html $RemoteRoot/templates/tienda/detalle_producto.html && mv /tmp/admin.py $RemoteRoot/tienda/admin.py && mv /tmp/variant_image_assignment.py $RemoteRoot/tienda/utils/variant_image_assignment.py && mv /tmp/__init__.py $RemoteRoot/tienda/management/__init__.py && mv /tmp/assign_variant_images.py $RemoteRoot/tienda/management/commands/assign_variant_images.py && mv /tmp/optimize_product_images.py $RemoteRoot/tienda/management/commands/optimize_product_images.py && mv /tmp/validate_variant_images.py $RemoteRoot/tienda/management/commands/validate_variant_images.py && touch $RemoteRoot/tienda/management/commands/__init__.py`""

Run "scp CULTCALLE/media/productos/OversizedTee_*.webp ${HostName}:$RemoteRoot/CULTCALLE/media/productos/"

Run "ssh $HostName `"cd $RemoteRoot && source venv/bin/activate && python manage.py assign_variant_images --product-id $ProductId && python manage.py validate_variant_images --product-id $ProductId && python manage.py check`""
Run "ssh $HostName `"systemctl restart cultclasiccs nginx && systemctl is-active cultclasiccs nginx`""

Write-Host "Deploy terminado." -ForegroundColor Green
