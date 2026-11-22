#!/usr/bin/env python3
"""Install or roll back the approved Besedina profile and certificate gallery."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import socket
import time
import urllib.error
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile


ARCHIVE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
ORIGIN = "https://azmednsk.ru"
HASHES = {'index.html': {'old': '2acbbf72f27019ba8873f1ad8ebf8b76a1cfc645526278bd54a67c38eb8ee370', 'new': '3107a636aec85deb5663d77851637db0621a9933abdfc4f57efbd5ffcd7310d3'}}
HTML_PATHS = frozenset(("index.html",))
ALLOWED_PATHS = frozenset(HASHES)
ASSET_PATHS = ALLOWED_PATHS - HTML_PATHS
REQUIRED_NEIGHBORS = ("index.html", "team/index.html", "sitemap.xml")
MAX_FILE = 20 * 1024 * 1024
MAX_PACKAGE = 64 * 1024 * 1024
MAX_NEIGHBOR_BYTES = 2 * 1024 * 1024 * 1024
MAX_NEIGHBOR_FILES = 50000
HEX = re.compile(r"[a-f0-9]{64}\Z")


class DeployError(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeployError("Повторяющийся ключ JSON: " + key)
        result[key] = value
    return result


def parse_json(data):
    return json.loads(data, object_pairs_hook=unique_object)


@dataclass(frozen=True)
class Bundle:
    hashes: dict
    files: dict


def retry_dns(read):
    for attempt in range(4):
        try:
            return read()
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, socket.gaierror) or exc.reason.errno != socket.EAI_AGAIN or attempt == 3:
                raise
            time.sleep((2, 5, 10)[attempt])


def fetch_live(path):
    try:
        return retry_dns(lambda: fetch_public(path))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def load_bundle(_):
    import zlib
    page = zlib.decompress(base64.b64decode('eJztPGtzI9WV3/0rLk1lx17UelmyZdlSIIQUbGBDBgipUNRUq/tK6nGru+luydY4rprxAJPszDIJSYoUGyCkUrsftrIxZoyN7ZmpYv+A9Bf4JXvOubefkm3ZYbOprfCQ1d33nvfz3ttae8Jw9GDoctYNelZzbg3/MEuzOw3F6yvNtS7XjOZajwca07ua5/OgofSDtlpT5F1b6/GGMjD5hut4gcJ0xw64DaM2TCPoNgw+MHWu0kWOmbYZmJql+rpm8UYpDcNzWk7gJyCYtsE3c23HspwNGBqYgcWbo1+O9se3RvujB6PD0cPRLhv9brQ7vjk6HN9no/8a3xzfg2d79OTrm79m41vjndGj0QmMwb/H8P/nKnzdhym7o8c4dryTY+N/hRkP4CH+/xCeH+JENtpjo4/gzt7oEeA8HH0G92/Ct6PR/lpB0JPkwOC+7pluYDp2go3TCZzk5TEOhK+HSDE8FCQcAd3iLiBmX/3n6BcA5h2ACHyMjsY749vIDRsdIPnwFQge3xvf/+r4LAbybPRvgOEOgEDI+2x0yFAgMH8f8OGTQ5i3Gz49oq+7JEKAejC+DdCQZJQaA3qBOsRN0B/R7ZMck5D2E1CIReR7F1EiOlLSLhANXIzfFZIS8AEV0PkBqQpVco8JOR2Rmm7BmGPgdEdOey8PZmKZ9jrzuNVQdM12bBNMTWFdj7cbSjcIXL9eKGg3etyw/fW81y8EXOsVWtznhmlrak/zTK2QgmLqqE0BoKD54AF+QV9cXC63yrpRqdQqpSKvVKqLeX/QgYnCApjv6TDcNwOev+5/e9DQbqiW03HUcrG8VCqVquqgpDCDt7nXXCuIOdKUXM9xuRcMG4rTqZONJW3pb8T6lWnE/t38/wbMf4oNQXhPxmXeQrOcOrTvWYmRF3CXSVBmT+vwc4BJbwLfiCBqLkAZcEM4SrG0knfthFshLw0FBlng12hoBct46roPBtfcUp4mZJuBUmcROl/v8p6Wd7yOkmPK0yQLePw6b72M9OE900hOOI/ZJ0F+rpyK4rrAVJyCiQLn/M04MtCU9Fsk7e8O+9dzWBC/ab8I9VYfbQqk7/XVq6/Rbf9lzQt+0IabW2cZ6ZOhQ2/DpJ5m2s/ZkDWGYlpo7y9zD53kUgaIs647rVcpFeHM94nvO+pfZJTEYk8y/RcFh8s54objrfvfc7xzxatDKWDqyvb2dpyo/WBocWYaEIoS5AFxbdPiKj1Vmvkpz7YM03ctbVjveKaxih9qwHtwJ+Cq7lj9nu3Xe6bd0zbni7lKsehuLrDoutT2FlY7mluv1NzNVc0yO7YKiu/5dT8AU1l1NQNwddSW5ejr9cWyu8mqS+7m9jRCmNnrRMTQhFWq0eulYvFbq11udrpBXesHzirgFuV7nehZdVrXuR6obTOoY7wFg1ttOZ7BPdXTDLPvA4TTcHZLW22YovrmDV7XLa3nzpeBldxivjrYyFUqwO0qSJurEn8pX6oCfq9j2vUiK7Iy4R9wrw0dgbrhgSg0e7jR5R6fjtBN4Cuh0NLQU8BLp4oq3+oHgWNH4jJtAtO2+KYEoAaOW0f4sbBIjhtd0I/qu5rO67bj9TRrFROUSrqr65AYubf9NBibqc3HU5cWV0ASW1Pt5xyTiUykDMLMGARKjy2eaRAxDYtLRTfiTjBM5jCDnBED+gp6wQyJO3IjD1pN3ev3WqpI2umUPjWjhwFO+U40+UXTD+AB+gV+fc7iPRCzUn8jioYK3n8BnsMw14HQSXmvlJOhURn9CkLZroh94/sSVoKAZHSAoHsO3HIM90MRLCka7p4Nl6LW+cAXY+AXCOfnIs6Gy+03E7Ev7oxIw36X8yDbH4E6QXEGUemr/tAHhDJolyt53feVmeFgalP7ttkeZgGkOq14ApSinrmuhaOrov8SV4ult7TJtmtWjrpaEEJdQhoisOUaSGqzrGQSQ5gPcGHE08zAD2fXlOYc+hGyZXKDYdZm+cQM8MELporyKakCs8Bkqtg+H30zecPtOoGTm20OXGmBM9DEpK1ENIyjC/TlfBWIlZfFKA5firAmhK5LETfjxDNTZSbiZzOnvCaGp2ROeSv0aJkSGKSTVTlETNV8F8d5GDpD4JeTmGm3L6zJGecY5mArodTtuTCzRfeWiyU0U8022HxKcMUw4Z3tEucWTN+a5gOYC7djaiK8y98Q2hhRWPn8NTxrRsOdaqrJ7A7FDyX27bk4Z0+LYmCPPuQgbuvDC8WxFLukghRNlwsCqRthfFXbHiTDJPSU25RWIzeDYA+3BjwqJutd0zA4VLKavt7xnL5t1J9st9vfEElnR5CwHJ0WIs+KIED6WfEjw3sqnIRsZSp39JMLMtpynHUoMSPuqChO5htB0er1vh9gDpfrQeHtjCVIXiuJ4pXaCSrOQ6pL2NhAxS6Jr0NIYb5jmQZ7slysFfVKhqsqTE3qVQ4CN3Y8oeWJmlzcMbjukPik1NI1rrjcEBQvFYuZ3gKp2MTRyIMkCO5cUr71Lqp7K8lGaXlpZaV9WXhtR+/76sD0zRZ0FU4/oAp/MRalwVt6aWVVPlGddhvqIBzwFwTSKaHgcjHJ545qmB7YNxWYYlG9uJLsvKPHKpZ3/laiUaPeSF63HGjteiJFTJnG+lbatPFDdJ74QVFfGKjoLv1A0CAMJjTh4lTQlrmVKR6SyXPaDC3bgopYMr0jjrDX0F0IHgSlruOC3HVfbWtvQSEMIcEKZSMdK3Y8LB0nfcxY5otG+7SeP4vC7/cA+nBL73s+uJvrmORfWd85o6nPQnS3Thkcmsrc2hOqyl574RWmqs05Bv+km1D07cJ1baCJuwrT/KGtiz4iaolcN983fd3pYVMENXNg6gXdz4OGsJ9Yb7y+9J3i8689+9qPOi/0f9wefv+ZH7/qt56333rOtwZXny+uKHF/kaZHXD3zExC+vw7WyCAu6utDZmsDs0PBhobN3B7RbBVmh1tLZXVQFh3SXMg3NTwTjdIpU6/7KdoliLAYCPMaxJHAh6+uOlhUmvPzC6zRZFtzV/o+B448Uw+urM5hyRAw13EsnzXYlnKjDwGsywdKnb0RUZLpjkqlijooFcKhaimPC+64YjfjhPJFJyxedELlohOqF52wdNEJyxedULvohJWLTigVLzxD6vpNmKJ3TW/dmcVS5MiZDSUcP6udhONnNZNw/KxWEo6vxsxjj+5gqb3uzCCAxOiZhZCcU44RG2bPDLwZ8caDZ0abmJLAGvY1M+CMWqBZMUYTZlV3NGFWfUcTZlV4NCGhcVu7MfRmMnc5cmYBhONn5T8cvxgTFy4HzkBdtHI4K3nRhIQ9WEOICHwmewiHqonQ4WoDazZrkiOTky3NnY1TMTA5tQsNhRH4sxEeD45AbIcJU+86ps4xZdp8g72kufMLq3Mbpm04G1AQXeU2FF4vS6g/WoRh7b5NZeK8SNYLkIiZABUuWjTEk7zr8YHp9H25LP4KVP9Q6q3OMbM9/0Q4mP30pyy6yPe0AKKzP38lvPOGoQWamiwGwidvXllYgJol6Hv2akgBtL6NCJbs/vJt04uW5p/tmpaR1y0omP/ZMfh84PX5QjTdpY3LBkDJI1qQKIhA7GbmqLJoUHnxhhj3puQE7xEX+CVvcbsTdBOUWTxgfc9qSEHnOzyYFwAWJAB4SlKUVKzzYeNKkuNBuX7lKSi+qWDLu1rQxXX4p67AXQEI4BAaS/ODht23LLwReEO2RXd87vsw85UAGswORwJwbX8e8CysbjMdRT5/DSjYjkhwaH/ebyQ4apbYt0kGIE8Lqur5fqPZf6LRQAwLrE6PEC2yKqe/8RKQmofi2fHm6aungVn15hf+UQ4IhYWSDA0x70fyyaFgIlYyXPgxF2JchhP4D/Xo8R5U8M8EUCW2+gGfvxLaExSoVxB4ocBe7XLE7nMby1ZmQu8DqDo2dJYbXdxxwtYFzNKoM9vB+lbrW2hrQAbj0B4wF/rPwM8LjFj5AkEAOrJEj8Nfnb9uBt15GAJowf22F8DTMru81Ezq3AugrwVuuJ9Zl5cNYNi40iab6LYSk2h7DJo26FPMILncYPF2EK5ElIvLbb2YXoIqt2ttfUoTpq/wIs82YbXkqkklXDXJdF7JZRacMGWL8oxmLMETgzl2ZlUrsUKyiKgFY9VqNblPWpWtYgLWlOUNXgXmW/E+YkGcRG05xpDpYOE+9SNyuQE6FtsJ1WaYg+Ya6DTd0fX0/FDDA6XY0G2gWRZKpcXlZeg+qwojJI1oZ62utUDQYJyrqKC6ugL/uJvQKVpBQ8H2iHAUYpxIHO4pIVpx8FVZXoJ2jQTdUGo1OXfGkzBKyCKeGVQmOjc6SdgC+11XdYtrdngeoizPIAJ1IYAWOnj6Fh/ylkcnamF0eNPtWz5XGJ7jbTmbDQV3xJeKrFyFYRjeGPR/LxVZabFbWrYq6hKrwne2rJZW2BIr1RjcgicoGhyNDeSg0xz9Ox6kAS5vsvHb49ujh8Dyu6NDPAaCB3PG9+Dvo9GXOXGOCPcn98UBEdqphCd4TIe2NGnaw/FdOoEz5dgJPD2UWoHu07EB92ySRvui8Ws+mL7VHP0a6LrPgIADeTjoeHx3/A4dNnqAB5wA2pcwiQZLjAnZ0iqrHoAcNNmrB9yqP7W8WFss1yrlSrmkNJ9aZvNwvcDgjlqG/0prBS3C/8no4dc3fzX6ZLxTZ8VavViEq3IJ/rKvDtjo09Fn+PT98S14upJ4miFJi/Tqmbj+kj0BK5cB8z1u4IoW+sS3oXX3GryoV6rlVntZr7b02soyr7VqenG5Vm7prdKS3voHreeuYhB1hpy/YDRWqkuLS1W66zt9T+eNisKguejgQfFrYKP2uiKWMWzHcbnNPYWF8R6PnjteuCTZUAQspRmfn0IVg5ZuoUbi82bjX45O2NfvfkBiK4SuZ2uDWOoFgJLaxqex2hnngPu+NeyYOO8jxE5HmPbwNBieBJthvtixj0KTjtsL4PSeqakQgT06A0nnBpvpcwApwAXnmq35SMTHmVN06WF6N3DU3lC1OHSPPRz/H+gL4C7oIuFhuJNziZbmShg/lKezdslb7qYRQrodaoTo93SQS7gVeIpEUhDS93G5OXIFUCtXW5pNSidJWFoLTWH0R8DyJXoWE6MwyDRHvwWF4zE8DAx4nG4f1Cye51mCdzkDPAUoIU/+DKS0L2VUcCFJ0HkdudwnLiLNyGeSOk3XuRtIYA/H94EtCDxrBTEKoBFLzTVczgYXFauxCV4sbrSG6VNgdCAjFQ9MEACbdmwmszyeDtPZpfJTn6b3mvBFibCOP7VQF4lKZKTZD46wNoe0CRHFgSoGuO5CcotzXKm8VFaY5WgGOTMHWwe1y2RYKleWYsdP5bPZGsmYF9EQxE1pAbQU8yUT8qnd0jwEnT62HXnpla+IlilR9sVZ/f9ESN+4cGJ+Mrlhclvo3Nj8V00jmbSRySozpQkREsIsDXVaaeLUJvnrxGtFay2v0DxVy5B1ShSyvpGjsBSzANinFH2/YHSOGDPPPitVRTgXY9L+n2xKlLRaE4+iPYNpJ7uSIApRFZyu6adthYK8PkXeiIO3KUPtUtLAngBSCRZbwNldcUabCqm36dj0rYnqb59hAIcJiUPXaV1QHSg1gfASmgAMX7/7Prguok3oWn7KeB0H7omoLBck1HAACAfMnl5RE3eUs4O91nL6WO11yxN2JR9hJj8B/rFmEWfPoSpFA8DD5anj5uP3wKzKzbk1Ny7eoAMGEH9OxZuMpebxDiXN8c+SFsNgLFkfWBOdOH8PT9efVjxDDS7OxmPFS8ClLh5jUhzfg4kI9xBw0Gn/EIlgYHxbHoBHlZ91wD6PnIAwGN1+SCf9pRU8nCy74Da9/DDpNffBl6jgobP4eMx+4tw+FvkPyfGofk+9DgBkfEjlEo4IwwWpBbklqxVxhBx4TxCYwpCjQQc4aA8pAZu8i1IhiGIIvU5wRAKGOo8hfCCV4D8Oq43xvRyZx+gLFANJGCQNA98hSaBVEHck3DwGgSnm8XscKdWPSqcaDiDuM+qojpHIR+SsjygUYfl2h4zoCOj8knqqd+hFC+yRPs8+jYIpSO1PxOsO+uxjkKtk5YSYlO9/CJkCvV9IrZ7y9gPKDz3/X0AOt0JOQa13hGIhJIiGK2GVaXe5n1FqXVwfpQRKL5LI9zWEuSUlmpOqgIcPqI7Eh5RGUBHSiPD5kXj5I6FOov0WESkh7oNZgwSTuI7AMdBx0tJHPzhFlZ/I91keZMyNTAudaw87YsKxj4ZyQjZ1U3gkBoRdIO8h8byT0iONFu/pwCVo8s+CdZTYAZjuHgYIEMfnIjKBmEZfoFc/lgU2euTJ+LYw+4Ta4PO9nBS88LzII/dkyN6lJYC7gPN90ggxeJKKTsjZRLw43bYw595KcL8vEIr+fFfoDrPDbSGb6OUf4fbjtykZ3UQ1EvgTEWV2pKYx30DIOSYkQooyCR1QcEKR4iVNAlM5hPuPKDaiA70nsTHyE1o/eA8T1Wka/w2ZNhKMQsDMT72VeHfoLHNHxGJimGJ/hh0VEo9yPSAFoFlAUMYZsqwQaTfHSMpQbQh0USgE1QLzIkcQaOye7xDO/UQoIFn+DilOuAHOQFvZB4GIEib1QhatoKAyxetdZLTobLnIb0kED8gZJVUHhD8VGB5SaJHheRcuDoiYT04LEii4pMEmzfUkGWfCzIdKHP+cYh+IFBwKoEdaQpM8EKENw9J9oPl+GDP2KCqSBY/v5iQ7Ihbthw4Lw5HgqF5FWxLCJ7O7I/3/INX+j2+T9VymoJk8RTS9oqETkdxQxQvHyYIm82T0KVGJojtKpFCUMLrlzzGOCIHIaqZv4Ql7XF+I3qQjW9hHS2BSayJeS5XlqFARJogLGaFeH4sEQjX/TuL1v8NUabADsgJ8c4T0gm8MnlUznFvYfHRWqiMrhO+ClD1ZjpExRdlKBs5T8/9pctmXxonxWvIgjZ685yY5zGGY0qjlwLxEUYLSyU3h7Ym2o4hsnkQh5rEIo2KV9gHEE3SSnwsrSOapw5AnsWp1PFXBsqoTmoBQ/Ch+jTLHBHGUKKGkEXl3L6mJ/YgorGqPBeWHJPMdYvVELiLvJu3g99Nf2gyNgapducr7BbUWu3KB6x7GbnwSvd8piJ+sK+KaVfJE+Tp67TSy+NRK+GGSyI8B2mdkmimzjDCfpv9EqosFlWivpoHNxWZDAx7E3FKmEynsMT3bl1XtcbIUo7C5Gy+QYqYQxbIU/Fqhjz8o4iZWEfu+1e+YBVy15LbJ1Rv9ljMoTFvKY6IYTllWrIEzPXHyDeRoXfDU+Cnf7pkeGum9n8m4mLwt1oypO5KJC3VwQhEw27XjNNw6nFjPoye65hksgs4t3vG0Xra7x2cYy7PLMUG+xwvP/OSa7a9faznBzAvxPe773O5wj7YrQpTYVGexih/DICmJE/Mww+uDBL7++I+yDT9FFqiWVyVwUog8fJrF8BbQhfQBfnF0tPnDqypp90Fifvgs3ufLvqgVMhK/sYV7Y9FSW7GSWJTDC1rni1FhzYPOnTalwyQXiXU8S7sxFGvYH4kSMa5gjqjIxEh3m4mYAhdvi6ZDGKUUxJTdpKlG0dM2Z7UHGEpbG4X2yovPO8Z3i/oPXnzuxWefvfrdZ9pBRXt+8/nSy0PH/eGwUvNf8mvfv/p6+3sbrw0Mv1z+3g/dn/g/uvFP65cxIkHiheznpWd+fL790KDLmY6Af67V4IHspMEkX/GTa6XfiA0BOf9L5iM+3axsbAd/8mP0C4B8IkgRCVOsA41vxs3WF7IYFu2O/BkDrFYOUxUu0XVIcTruCvazdWrbcfB9FEmMuJKmMesOLe17Tv1NiBwmoWPxuxAkXJBJjtWq4XogfU7utiJAfKPtab6JP1fiB3TIO1pEFESm/BD8XbOUyVuyoE5kOFcLTCi/RU4TtZ+sRjK7cdFIw+k4AweEMvp1sjmSKyeyQY+LgcfUl+6EVUl6e5xafbiNqxCQzW+PPp+O0/VM3lPllTMQCRhkep/K6KNUnye6rJgRoG86UMvUuQ3CAG0cykL3QCTulPVnJjuWGZi6poqfpKFC4JhM4lDUfrKvkIuYR1EFTz14VOgIYTwgMdHFdCzTEIiCHdewHxCH79KKhSyfxAJAZu813H/MbmKmh0ERUSBhCNojR5vYTC6sOz3X4uu+KsujaIf4MVVXR9S13g3bb7AREsgJ1pdpQB5Ypufo3XVNbNTSIgl56x3kNbPTHNwwqfbCqpNWgCQ4CiCjj/Ff/OmWPwExn4LYfgMA/wDf/gAPfvfVMXnlx6Nfwd2P6qxUWqlWlpeKS0vFZXFQ4bfgsvCgWiku1YrFSnGF7n4IgD4Rd4ulYrEUxiyxvWqYmuV0qL4K96OivVzpdmKIqlu4eJLZ4E3tMH9A+9g3cY1yfE9p/vcH8Z5u8nSMQEMJFou7xXhT6WfJdZizfsMFirxFuRd0F+tsUvFOWCGLipbq8Hjhi0z3mMznYdxjUeO0R89k8N+bugqgpbey/98d7ZB2Gq6TdXG/6aKHaaJciOZy6k8FpN/ov+5P/DSAeFmHihsSpkqH08L3zWriTR20J7RZOeYqDnndNEBM3wVpSJmkzqJdXkWmnwDfEKWTYJZNednnDHw6BB0QrA2CIJ7E57UNAnxNWBZJZOJXH9OyKZwjnAIe2MNTOvjzkv8D4uFwtA=='))
    if sha(page) != HASHES["index.html"]["new"]:
        raise DeployError("Повреждён профиль")
    return Bundle(HASHES, {"index.html": page})


@dataclass(frozen=True)
class Directive:
    words: tuple
    children: tuple | None = None


def nginx_tokens(text):
    result, word = [], []
    i, quote = 0, None
    while i < len(text):
        char = text[i]
        if char == "$" and text[i:i + 2] == "${":
            end = text.find("}", i + 2)
            if end < 0 or not re.fullmatch(r"[A-Za-z0-9_]+", text[i + 2:end]):
                raise DeployError("Некорректная braced variable nginx")
            word.append(text[i:end + 1])
            i = end + 1
            continue
        if char == "\\":
            if i + 1 >= len(text):
                raise DeployError("Оборванная escape-последовательность nginx")
            following = text[i + 1]
            # Preserve regex escapes; only unescape whitespace and quoted delimiters.
            if following.isspace() or following in "\\\"'{};#":
                word.append(following)
            else:
                word.extend((char, following))
            i += 2
            continue
        if quote:
            if char == quote:
                quote = None
            else:
                word.append(char)
        elif char in "\"'":
            quote = char
        elif char == "#":
            if word:
                result.append("".join(word))
                word = []
            stop = text.find("\n", i)
            i = len(text) if stop < 0 else stop
            continue
        elif char.isspace() or char in "{};":
            if word:
                result.append("".join(word))
                word = []
            if char in "{};":
                result.append(char)
        else:
            word.append(char)
        i += 1
    if quote:
        raise DeployError("Незакрытая строка nginx")
    if word:
        result.append("".join(word))
    return result


def nginx_parse(text):
    tokens = nginx_tokens(text)
    index = 0

    def block(nested=False):
        nonlocal index
        nodes, words = [], []
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == "}":
                if not nested or words:
                    raise DeployError("Неоднозначная структура nginx")
                return tuple(nodes)
            if token == ";":
                if not words:
                    raise DeployError("Пустая директива nginx")
                nodes.append(Directive(tuple(words)))
                words = []
            elif token == "{":
                if not words:
                    raise DeployError("Блок nginx без имени")
                nodes.append(Directive(tuple(words), block(True)))
                words = []
            else:
                words.append(token)
        if nested or words:
            raise DeployError("Незавершённая структура nginx")
        return tuple(nodes)

    return block()


def nginx_expanded(dump):
    markers = list(re.finditer(r"^# configuration file (.+):\s*$", dump, re.M))
    if not markers:
        return nginx_parse(dump)
    files = {}
    order = []
    for position, marker in enumerate(markers):
        name = marker.group(1)
        if name in files or not name.startswith("/"):
            raise DeployError("Повторяющийся или относительный источник nginx -T")
        end = markers[position + 1].start() if position + 1 < len(markers) else len(dump)
        files[name] = nginx_parse(dump[marker.end():end])
        order.append(name)
    prefix = str(PurePosixPath(order[0]).parent)

    def expand(nodes, stack):
        if len(stack) > 30:
            raise DeployError("Слишком глубокие include nginx")
        result = []
        for node in nodes:
            if node.words[0] == "include":
                if len(node.words) != 2 or node.children is not None or "$" in node.words[1]:
                    raise DeployError("Неподдерживаемый include nginx")
                pattern = node.words[1]
                if not pattern.startswith("/"):
                    pattern = prefix + "/" + pattern
                matches = sorted(name for name in files if fnmatch.fnmatchcase(name, pattern))
                if not matches and not any(c in pattern for c in "*?["):
                    raise DeployError("nginx -T не содержит include: " + pattern)
                for name in matches:
                    if name in stack:
                        raise DeployError("Циклический include nginx")
                    result.extend(expand(files[name], stack + (name,)))
            else:
                children = expand(node.children, stack) if node.children is not None else None
                result.append(Directive(node.words, children))
        return tuple(result)

    return expand(files[order[0]], (order[0],))


def public_path(name):
    path = "/team/besedina-maria/" + name
    return path[:-len("index.html")] if name in HTML_PATHS else path


def location_applies(words, paths):
    args = words[1:]
    if len(args) == 1:
        return any(p.startswith(args[0]) for p in paths)
    if len(args) != 2:
        raise DeployError("Неоднозначная location nginx")
    modifier, pattern = args
    if modifier == "=":
        return pattern in paths
    if modifier == "^~":
        return any(p.startswith(pattern) for p in paths)
    if modifier in ("~", "~*"):
        try:
            return any(re.search(pattern, p, re.I if modifier == "~*" else 0) for p in paths)
        except re.error as exc:
            raise DeployError("Не удалось безопасно разобрать regex location") from exc
    raise DeployError("Неподдерживаемый модификатор location nginx")


ROUTING_DIRECTIVES = frozenset(("root", "alias", "proxy_pass", "fastcgi_pass", "uwsgi_pass",
                                "scgi_pass", "grpc_pass", "rewrite", "return"))


def known_nginx_condition(words):
    """Evaluate only simple conditions for the exact apex GET requests we make.

    Unknown operands and unsupported regular expressions return None. They must
    never be assumed false when their body can change routing.
    """
    expression = " ".join(words[1:]).strip()
    if not (expression.startswith("(") and expression.endswith(")")):
        return None
    match = re.fullmatch(
        r"\s*(\$(?:host|request_method)|\$\{(?:host|request_method)\})\s+"
        r"(=|!=|~\*?|!~\*?)\s+(.+?)\s*", expression[1:-1], re.S)
    if not match:
        return None
    variable, operator, rhs = match.groups()
    name = variable[2:-1] if variable.startswith("${") else variable[1:]
    value = {"host": "azmednsk.ru", "request_method": "GET"}[name]
    if operator in ("=", "!="):
        if "$" in rhs:
            return None
        equal = value == rhs
        return equal if operator == "=" else not equal
    # A trailing regex '$' is an anchor; '$name' may be a dynamic operand.
    if len(rhs) > 256 or re.search(r"\$(?:[A-Za-z_]|\{)", rhs):
        return None
    try:
        matched = re.search(rhs, value, re.I if operator.endswith("*") else 0) is not None
    except re.error:
        return None
    return not matched if operator.startswith("!") else matched


def contains_routing(nodes):
    return any(n.words[0] in ROUTING_DIRECTIVES or
               (n.children is not None and contains_routing(n.children)) for n in nodes)


def parse_nginx_root(dump):
    nodes = nginx_expanded(dump)
    http = [n for n in nodes if n.words == ("http",) and n.children is not None]
    if len(http) > 1:
        raise DeployError("Несколько http-контекстов nginx")
    context = http[0].children if http else nodes
    inherited = [n.words[1:] for n in context if n.words[0] == "root"]
    candidates = []
    for server in context:
        if server.words != ("server",) or server.children is None:
            continue
        names = [v for n in server.children if n.words[0] == "server_name" for v in n.words[1:]]
        listens = [n.words[1:] for n in server.children if n.words[0] == "listen"]
        https = any(args and (args[0] == "443" or args[0].endswith(":443")) for args in listens)
        if "azmednsk.ru" not in names or not https:
            continue
        roots = [n.words[1:] for n in server.children if n.words[0] == "root"] or inherited
        if len(roots) != 1 or len(roots[0]) != 1:
            raise DeployError("HTTPS-vhost не имеет одного однозначного root")
        root = roots[0][0]
        if not root.startswith("/") or "$" in root or ".." in PurePosixPath(root).parts:
            raise DeployError("Динамический или относительный root nginx")
        paths = {public_path(p) for p in ALLOWED_PATHS} | {"/team/besedina-maria/"}

        def validate(nodes_to_check):
            for node in nodes_to_check:
                key = node.words[0]
                if key == "location":
                    if node.words[1:2] and node.words[1].startswith("@"):
                        continue
                    if location_applies(node.words, paths):
                        validate(node.children or ())
                elif key == "if":
                    applies = known_nginx_condition(node.words)
                    if applies is False:
                        continue
                    if applies is None and contains_routing(node.children or ()):
                        raise DeployError("Неоднозначное if nginx с изменением маршрутизации")
                    validate(node.children or ())
                elif key == "root":
                    if node.words[1:] != (root,):
                        raise DeployError("Root /cases/ переопределён в location")
                elif key in ("alias", "proxy_pass", "fastcgi_pass", "uwsgi_pass", "scgi_pass",
                             "grpc_pass", "rewrite", "return"):
                    raise DeployError("Маршрут /cases/ содержит " + key + "; требуется адресная проверка")
                elif node.children is not None:
                    validate(node.children)
        validate(server.children)
        candidates.append(Path(root))
    if len(candidates) != 1:
        raise DeployError("Для azmednsk.ru:443 найдено HTTPS-vhost: " + str(len(candidates)))
    return candidates[0]


def discover_root():
    try:
        result = subprocess.run(["nginx", "-T"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeployError("Не удалось выполнить nginx -T") from exc
    if result.returncode or "conflicting server name" in result.stderr.lower():
        raise DeployError("nginx -T завершился ошибкой или конфликтом server_name")
    return parse_nginx_root(result.stdout)


def safe_absolute(path, may_be_missing=False):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise DeployError("Требуется абсолютный путь без '..'")
    current = Path(path.anchor)
    missing = False
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise DeployError("Символическая ссылка в пути: " + str(current))
        if not current.exists():
            missing = True
        elif missing:
            raise DeployError("Путь изменился во время проверки")
    if missing and not may_be_missing:
        raise DeployError("Путь отсутствует: " + str(path))
    return path


def target_path(cases_root, name):
    if name not in ALLOWED_PATHS:
        raise DeployError(f"Путь вне разрешённых {len(ALLOWED_PATHS)} файлов: " + str(name))
    return safe_absolute(cases_root / name, may_be_missing=True)


def read_regular(path, missing=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if missing:
            return None, None
        raise DeployError("Отсутствует файл: " + str(path))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DeployError("Не обычный файл: " + str(path))
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read()
        return data, {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
                      "xattrs": capture_xattrs(fd)}
    finally:
        os.close(fd)


def capture_xattrs(fd):
    try:
        return {name: base64.b64encode(os.getxattr(fd, name)).decode("ascii")
                for name in sorted(os.listxattr(fd))}
    except (OSError, AttributeError) as exc:
        raise DeployError("Не удалось прочитать extended attributes/ACL; запись запрещена") from exc


def restore_xattrs(fd, expected):
    try:
        for name in set(os.listxattr(fd)) - set(expected):
            os.removexattr(fd, name)
        for name, encoded in expected.items():
            os.setxattr(fd, name, base64.b64decode(encoded, validate=True))
        if capture_xattrs(fd) != expected:
            raise DeployError("Проверка extended attributes/ACL не пройдена")
    except (OSError, AttributeError, ValueError) as exc:
        raise DeployError("Не удалось сохранить extended attributes/ACL; замена запрещена") from exc


def current_hash(path):
    data, _ = read_regular(path, missing=True)
    return sha(data) if data is not None else None


def inspect_targets(cases_root, bundle):
    states = {}
    for name in sorted(ALLOWED_PATHS):
        path = target_path(cases_root, name)
        data, metadata = read_regular(path, missing=True)
        digest = sha(data) if data is not None else None
        if digest not in (bundle.hashes[name]["old"], bundle.hashes[name]["new"]):
            raise DeployError("Файл отличается от old/new manifest: " + name)
        states[name] = {"data": data, "metadata": metadata, "sha256": digest,
                        "pending": digest != bundle.hashes[name]["new"]}
    return states


def snapshot_neighbors(web_root):
    for name in REQUIRED_NEIGHBORS:
        path = safe_absolute(web_root / name)
        if not path.is_file():
            raise DeployError("Нет обязательного соседнего файла: " + name)
    excluded = {"team/besedina-maria/" + p for p in ALLOWED_PATHS}
    result, total = {}, 0
    for parent, directories, files in os.walk(web_root, followlinks=False):
        parent = Path(parent)
        for name in list(directories):
            path = parent / name
            if path.is_symlink():
                result[path.relative_to(web_root).as_posix()] = {"kind": "symlink", "target": os.readlink(path)}
                directories.remove(name)
        for name in sorted(files):
            path = parent / name
            rel = path.relative_to(web_root).as_posix()
            if rel in excluded:
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[rel] = {"kind": "symlink", "target": os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > MAX_NEIGHBOR_BYTES or len(result) >= MAX_NEIGHBOR_FILES:
                    raise DeployError("Защитный снимок превышает лимит; нужна адресная проверка root")
                digest = hashlib.sha256()
                with open(path, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                result[rel] = {"kind": "file", "sha256": digest.hexdigest(),
                               "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
            else:
                raise DeployError("Специальный файл внутри web-root: " + rel)
    return result


def assert_neighbors(web_root, expected):
    actual = snapshot_neighbors(web_root)
    if actual != expected:
        changes = sorted(k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k))
        raise DeployError("Изменились соседние файлы: " + ", ".join(changes[:8]))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_public(path):
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(ORIGIN + path, headers={
        "User-Agent": "AZMEDNSK-Gallery-Verification/1.0", "Accept-Encoding": "identity",
        "Cache-Control": "no-cache", "Pragma": "no-cache",
    })
    with opener.open(request, timeout=20) as response:
        if response.status != 200 or response.geturl() != ORIGIN + path:
            raise DeployError("HTTPS вернул неожиданный статус или перенаправление")
        body = response.read(MAX_FILE + 1)
    if len(body) > MAX_FILE:
        raise DeployError("HTTPS ответ превышает лимит")
    return body


def verify_https(expected, fetcher):
    def verify(item):
        name, digest = item
        try:
            data = fetcher(public_path(name))
            if (None if data is None else sha(data)) != digest:
                raise DeployError("HTTPS hash не совпадает")
        except Exception as exc:
            raise DeployError("HTTPS проверка не пройдена: " + public_path(name) + "; " + str(exc)) from exc
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(verify, sorted(expected.items())))


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_parent(path):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    safe_absolute(parent)
    for folder in reversed(missing):
        folder.mkdir(mode=0o755)
        fsync_dir(folder.parent)


def atomic_write(path, data, expected, metadata):
    safe_absolute(path, may_be_missing=True)
    previous, previous_metadata = read_regular(path, missing=True)
    if (sha(previous) if previous is not None else None) != expected:
        raise DeployError("Файл изменился перед записью: " + str(path))
    if previous_metadata is not None and previous_metadata != metadata:
        raise DeployError("Метаданные/ACL изменились перед записью: " + str(path))
    ensure_parent(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".az-cases-gallery-", delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write(data)
            out.flush()
            if os.geteuid() == 0:
                os.fchown(out.fileno(), metadata["uid"], metadata["gid"])
            os.fchmod(out.fileno(), metadata["mode"])
            if "xattrs" in metadata:
                restore_xattrs(out.fileno(), metadata["xattrs"])
            os.fsync(out.fileno())
            safe_absolute(path, may_be_missing=True)
            current, current_metadata = read_regular(path, missing=True)
            if ((sha(current) if current is not None else None) != expected or
                    current_metadata != previous_metadata):
                raise DeployError("Файл изменился во время подготовки записи: " + str(path))
            os.replace(temporary, path)
            fsync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def save_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    previous, metadata = read_regular(path, missing=True)
    if metadata is None:
        metadata = {"mode": 0o600, "uid": os.geteuid(), "gid": os.getegid()}
    atomic_write(path, data, sha(previous) if previous is not None else None, metadata)


def planned_directories(cases_root, states):
    missing = set()
    for name, state in states.items():
        if not state["pending"]:
            continue
        parent = target_path(cases_root, name).parent
        while parent != cases_root and not parent.exists():
            missing.add(parent.relative_to(cases_root).as_posix())
            parent = parent.parent
    return sorted(missing, key=lambda p: (p.count("/"), p))


def make_backup(web_root, backups, bundle, states, neighbors):
    safe_absolute(backups, may_be_missing=True)
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backups / ("besedina-profile-" + stamp)
    backup.mkdir(mode=0o700)
    entries = {}
    for name, state in states.items():
        item = {**bundle.hashes[name], "before": state["sha256"],
                "pending": state["pending"], "metadata": state["metadata"]}
        if state["data"] is not None:
            saved = backup / "files" / name
            saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(saved, "xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(state["data"])
                stream.flush()
                os.fsync(stream.fileno())
            if current_hash(saved) != state["sha256"]:
                raise DeployError("Не прошла проверка резервной копии: " + name)
            item["backup_file"] = "files/" + name
        entries[name] = item
    source = Path(__file__).read_bytes()
    rollback = backup / "rollback.py"
    with open(rollback, "xb") as stream:
        os.fchmod(stream.fileno(), 0o700)
        stream.write(source)
        stream.flush()
        os.fsync(stream.fileno())
    manifest = {
        "schema": 1, "archive_sha256": ARCHIVE_SHA256,
        "web_root": str(web_root), "cases_root": str(web_root / "team/besedina-maria"),
        "created_utc": stamp, "status": "prepared", "entries": entries,
        "new_files": sorted(n for n, s in states.items() if s["pending"] and s["data"] is None),
        "created_directories": planned_directories(web_root / "team/besedina-maria", states),
        "neighbors_before": neighbors, "rollback_script_sha256": sha(source),
    }
    save_json(backup / "manifest.json", manifest)
    for parent, directories, _ in os.walk(backup, topdown=False):
        fsync_dir(Path(parent))
    fsync_dir(backups)
    return backup, manifest


def load_rollback_manifest(backup):
    safe_absolute(backup)
    manifest = parse_json(read_regular(backup / "manifest.json")[0])
    if manifest.get("schema") != 1 or manifest.get("archive_sha256") != ARCHIVE_SHA256:
        raise DeployError("Неизвестный manifest резервной копии")
    if set(manifest.get("entries", {})) != ALLOWED_PATHS:
        raise DeployError(f"Rollback manifest выходит за {len(ALLOWED_PATHS)} разрешённых пути")
    web_root = safe_absolute(Path(manifest["web_root"]))
    if manifest.get("cases_root") != str(web_root / "team/besedina-maria"):
        raise DeployError("Несовпадение root в rollback manifest")
    if current_hash(backup / "rollback.py") != manifest.get("rollback_script_sha256"):
        raise DeployError("Rollback script повреждён")
    for name, item in manifest["entries"].items():
        if not isinstance(item.get("new"), str) or not HEX.fullmatch(item["new"]):
            raise DeployError("Неверный hash rollback manifest")
        if item.get("before") not in (item.get("old"), item["new"]):
            raise DeployError("Неверный исходный hash rollback manifest")
        if item.get("pending") != (item["before"] != item["new"]):
            raise DeployError("Неверный pending rollback manifest")
        if item["before"] is not None:
            if item.get("backup_file") != "files/" + name:
                raise DeployError("Неверный путь резервной копии")
            saved = safe_absolute(backup / item["backup_file"])
            if current_hash(saved) != item["before"]:
                raise DeployError("Hash резервной копии не совпадает: " + name)
    expected_new = sorted(n for n, e in manifest["entries"].items() if e["pending"] and e["before"] is None)
    if manifest.get("new_files") != expected_new:
        raise DeployError("Неверный список новых файлов rollback")
    possible_dirs = {p.as_posix() for n in ALLOWED_PATHS for p in PurePosixPath(n).parents if p.as_posix() != "."}
    if not set(manifest.get("created_directories", ())).issubset(possible_dirs):
        raise DeployError("Неверный список каталогов rollback")
    return manifest, web_root


def rollback_backup(backup, *, locked=False, log=print):
    backup = Path(backup)
    manifest, web_root = load_rollback_manifest(backup)
    root_fd = None
    if not locked:
        root_fd = os.open(web_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(root_fd, fcntl.LOCK_EX)
    try:
        cases_root = safe_absolute(web_root / "team/besedina-maria")
        neighbors = snapshot_neighbors(web_root)
        # Check every destination before restoring even the first file.
        for name, item in manifest["entries"].items():
            data, metadata = read_regular(target_path(cases_root, name), missing=True)
            actual = sha(data) if data is not None else None
            if actual not in (item["before"], item["new"]):
                raise DeployError("Rollback остановлен: current не равен saved/new: " + name)
            if not item["pending"] and actual != item["before"]:
                raise DeployError("Rollback остановлен: изменён нетронутый файл: " + name)
            if item["before"] is not None and metadata != item["metadata"]:
                raise DeployError("Rollback остановлен: метаданные/ACL изменились: " + name)
        order = sorted(manifest["entries"], key=lambda n: (n not in HTML_PATHS, n))
        for name in order:
            item = manifest["entries"][name]
            path = target_path(cases_root, name)
            if not item["pending"] or current_hash(path) == item["before"]:
                continue
            if current_hash(path) != item["new"]:
                raise DeployError("Rollback: файл изменился: " + name)
            if item["before"] is None:
                path.unlink()
                fsync_dir(path.parent)
            else:
                data = read_regular(backup / item["backup_file"])[0]
                atomic_write(path, data, item["new"], item["metadata"])
        for rel in sorted(manifest["created_directories"], key=lambda p: (-p.count("/"), p)):
            folder = safe_absolute(cases_root / rel, may_be_missing=True)
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
                fsync_dir(folder.parent)
        for name, item in manifest["entries"].items():
            if current_hash(target_path(cases_root, name)) != item["before"]:
                raise DeployError("Rollback verification failed: " + name)
        assert_neighbors(web_root, neighbors)
        manifest["status"] = "rolled_back"
        save_json(backup / "manifest.json", manifest)
        log(f"ROLLBACK=PASS; FILES_VERIFIED={len(ALLOWED_PATHS)}; NEIGHBORS=UNCHANGED")
        return {"status": "rolled_back", "backup": str(backup)}
    finally:
        if root_fd is not None:
            os.close(root_fd)


def deploy(archive_path, web_root, backups, *, apply=False, fetcher=fetch_public, fault=None, log=print):
    """Local root/backups/fetcher injection is for tests, not exposed as CLI flags."""
    bundle = load_bundle(archive_path)
    web_root = safe_absolute(Path(web_root))
    backups = Path(backups)
    if backups == web_root or web_root in backups.parents:
        raise DeployError("Backup должен находиться вне web-root")
    cases_root = safe_absolute(web_root / "team/besedina-maria")
    if not web_root.is_dir() or not cases_root.is_dir():
        raise DeployError("Отсутствует опубликованный каталог кейсов")
    root_fd = os.open(web_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        states = inspect_targets(cases_root, bundle)
        neighbors = snapshot_neighbors(web_root)
        verify_https({n: states[n]["sha256"] for n in HTML_PATHS}, fetcher)
        # HTTPS may take time: repeat all local guards before even creating backups.
        if any(current_hash(target_path(cases_root, n)) != s["sha256"] for n, s in states.items()):
            raise DeployError("Файлы изменились во время HTTPS preflight")
        assert_neighbors(web_root, neighbors)
        pending = sorted((n for n, s in states.items() if s["pending"]), key=lambda n: (n in HTML_PATHS, n))
        log("ROOT=" + str(web_root))
        log("ARCHIVE_SHA256=" + ARCHIVE_SHA256)
        log(f"PREFLIGHT=PASS; HTTPS_PAGES={len(HTML_PATHS)}; ALLOWED_FILES={len(ALLOWED_PATHS)}; PENDING=" + str(len(pending)))
        if not apply:
            return {"status": "check_only", "pending": len(pending), "web_root": str(web_root)}
        if not pending:
            verify_https({n: bundle.hashes[n]["new"] for n in ALLOWED_PATHS}, fetcher)
            assert_neighbors(web_root, neighbors)
            log(f"PROFILE=ALREADY_INSTALLED; FILES_VERIFIED={len(ALLOWED_PATHS)}; HTTPS_FILES={len(ALLOWED_PATHS)}; NEIGHBORS=UNCHANGED")
            return {"status": "already_installed", "pending": 0, "web_root": str(web_root)}
        backup, manifest = make_backup(web_root, backups, bundle, states, neighbors)
        log("BACKUP=" + str(backup))
        log("ROLLBACK_COMMAND=python3 " + shlex.quote(str(backup / "rollback.py")))
        assert_neighbors(web_root, neighbors)
        for name, state in states.items():
            if current_hash(target_path(cases_root, name)) != state["sha256"]:
                raise DeployError("Файл изменился при резервном копировании: " + name)
        owner = cases_root.stat()
        new_metadata = {"mode": 0o644, "uid": owner.st_uid, "gid": owner.st_gid}
        manifest["status"] = "applying"
        save_json(backup / "manifest.json", manifest)
        try:
            for name in pending:
                if fault:
                    fault("before_write", name)
                atomic_write(target_path(cases_root, name), bundle.files[name], states[name]["sha256"],
                             states[name]["metadata"] or new_metadata)
                if fault:
                    fault("after_write", name)
            for name in ALLOWED_PATHS:
                if current_hash(target_path(cases_root, name)) != bundle.hashes[name]["new"]:
                    raise DeployError("Локальный postcheck не пройден: " + name)
            assert_neighbors(web_root, neighbors)
            verify_https({n: bundle.hashes[n]["new"] for n in ALLOWED_PATHS}, fetcher)
            assert_neighbors(web_root, neighbors)
            manifest["status"] = "applied"
            save_json(backup / "manifest.json", manifest)
        except BaseException as error:
            try:
                rollback_backup(backup, locked=True, log=log)
                assert_neighbors(web_root, neighbors)
            except BaseException as rollback_error:
                log("ROLLBACK=BLOCKED; BACKUP=" + str(backup))
                raise DeployError("Ошибка установки: " + str(error) + "; rollback: " + str(rollback_error)) from error
            raise DeployError(f"Установка отменена, исходные {len(ALLOWED_PATHS)} пути восстановлены: " + str(error)) from error
        log(f"PROFILE=PASS; FILES_VERIFIED={len(ALLOWED_PATHS)}; HTTPS_FILES={len(ALLOWED_PATHS)}; NEIGHBORS=UNCHANGED")
        return {"status": "applied", "pending": len(pending), "backup": str(backup), "web_root": str(web_root)}
    finally:
        os.close(root_fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and os.geteuid() != 0:
        raise DeployError("Для установки требуется root")
    deploy(None, discover_root(), Path("/var/backups/azmednsk"), apply=args.apply, fetcher=fetch_live)


if __name__ == "__main__":
    try:
        if Path(__file__).name == "rollback.py":
            rollback_backup(Path(__file__).resolve().parent)
        else:
            main()
    except (Exception, KeyboardInterrupt) as error:
        sys.exit("STOP=" + str(error))
