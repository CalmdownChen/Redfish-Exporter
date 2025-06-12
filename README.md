# It's a redfish exporter

## start service
```
cp redfish_exporter.service /etc/systemd/system/redfish_exporter.service
sudo systemctl daemon-reload
sudo systemctl enable redfish_exporter.service
sudo systemctl start redfish_exporter.service
```

## logrotate example

```
vim /etc/logrotate.d/redfish_exporter
```

```
/home/ding/prome/log/redfish_exporter.log /home/ding/prome/log/redfish_exporter.err {
    su ding ding
    size 5M
    rotate 7
    compress
    missingok
    notifempty
    create 640 ding ding
    sharedscripts
    postrotate
        systemctl kill -s HUP redfish_exporter >/dev/null 2>&1 || true
    endscript
}
```
