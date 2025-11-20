# ONVIF Proxy for T-Link Tapo C500 Integration with Frigate

Simple onvif proxy to fix MoveStatus and RelativeMove commands for Tapo C55 cameras

## Installation

```shell
python3 -m venv .venv
```

```shell
source .venv\bin\activate
```

```shell
python3 -m pip install -r requirements.txt
```

Copy the .env.example to .env and configure it with your credentials and ip addresses.

## Use with onvif client

```shell
python3 onvif_proxy.py
```

Dockerfile for onvif proxy included

## Credits

This project was inspired by and includes ideas/code adapted from eporsche's
[frigate-to-baby1t-onvif-proxy](https://github.com/eporsche/frigate-to-baby1t-onvif-proxy).
Thanks to the original author for the reference implementation and helpful ideas.
