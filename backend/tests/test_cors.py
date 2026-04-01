# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import re

from api import cors


def test_parse_additional_origins_normalizes_and_filters_invalid_entries():
    origins = cors.parse_additional_origins(
        " http://10.96.11.5:3008/ , https://Example.COM , not-an-origin "
    )

    assert origins == {
        "http://10.96.11.5:3008",
        "https://example.com",
    }


def test_build_private_network_origin_regex_matches_common_local_origins():
    pattern = re.compile(cors.build_private_network_origin_regex("3008"))

    assert pattern.fullmatch("http://localhost:3008")
    assert pattern.fullmatch("http://127.0.0.1:3008")
    assert pattern.fullmatch("http://10.96.11.5:3008")
    assert pattern.fullmatch("http://172.20.1.15:3008")
    assert pattern.fullmatch("http://192.168.31.9:3008")
    assert pattern.fullmatch("http://[::1]:3008")

    assert not pattern.fullmatch("http://10.96.11.5:3009")
    assert not pattern.fullmatch("http://8.8.8.8:3008")
    assert not pattern.fullmatch("https://example.com:3008")


def test_build_allow_all_origin_regex_matches_http_and_https_origins():
    pattern = re.compile(cors.build_allow_all_origin_regex())

    assert pattern.fullmatch("http://localhost:3008")
    assert pattern.fullmatch("http://8.8.8.8:9000")
    assert pattern.fullmatch("https://frontend.example.com")

    assert not pattern.fullmatch("ftp://frontend.example.com")
    assert not pattern.fullmatch("null")


def test_build_cors_settings_adds_discovered_hosts_ips_and_explicit_origins(monkeypatch):
    monkeypatch.setattr(cors, "discover_local_hostnames", lambda: {"localhost", "gpu005"})
    monkeypatch.setattr(
        cors,
        "discover_local_ip_addresses",
        lambda: {"10.96.11.5", "::1"},
    )

    origins, origin_regex = cors.build_cors_settings(
        frontend_port="3008",
        additional_origins="https://frontend.example.com, http://10.0.0.8:3008/",
        allow_private_networks=True,
    )

    assert "http://localhost:3008" in origins
    assert "http://127.0.0.1:3008" in origins
    assert "http://0.0.0.0:3008" in origins
    assert "http://gpu005:3008" in origins
    assert "http://10.96.11.5:3008" in origins
    assert "http://[::1]:3008" in origins
    assert "https://frontend.example.com" in origins
    assert "http://10.0.0.8:3008" in origins
    assert origin_regex is not None


def test_build_cors_settings_can_disable_private_network_regex(monkeypatch):
    monkeypatch.setattr(cors, "discover_local_hostnames", lambda: {"localhost"})
    monkeypatch.setattr(cors, "discover_local_ip_addresses", lambda: set())

    _, origin_regex = cors.build_cors_settings(
        frontend_port="3008",
        allow_private_networks=False,
    )

    assert origin_regex is None


def test_build_cors_settings_can_allow_all_origins(monkeypatch):
    monkeypatch.setattr(cors, "discover_local_hostnames", lambda: {"localhost"})
    monkeypatch.setattr(cors, "discover_local_ip_addresses", lambda: {"10.96.11.5"})

    origins, origin_regex = cors.build_cors_settings(
        frontend_port="3008",
        additional_origins="https://frontend.example.com",
        allow_private_networks=False,
        allow_all_origins=True,
    )

    assert origins == ["https://frontend.example.com"]
    assert origin_regex is not None

    pattern = re.compile(origin_regex)
    assert pattern.fullmatch("http://127.0.0.1:3008")
    assert pattern.fullmatch("https://frontend.example.com")
