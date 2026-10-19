-- Synthetic IntelMQ events for tests. Documentation-only IPs (RFC 5737),
-- private ASNs (RFC 5398) and example.* domains (RFC 2606). No real data.
INSERT INTO events ("time.source", "time.observation", "feed.name", "feed.provider",
    "classification.taxonomy", "classification.type", "classification.identifier",
    "source.ip", "source.port", "source.asn", "source.as_name", "source.fqdn",
    "source.geolocation.cc", "malware.name", "severity", "tlp", "raw")
VALUES
 (now() - interval '1 hour',  now(), 'Shadowserver Open-DNS', 'Shadowserver', 'vulnerable', 'vulnerable-system', 'openresolver', '192.0.2.10', 53, 64496, 'EXAMPLE-NET-A', NULL, 'AT', NULL, 'info', 'AMBER', 'cmF3'),
 (now() - interval '2 hours', now(), 'Shadowserver Open-DNS', 'Shadowserver', 'vulnerable', 'vulnerable-system', 'openresolver', '192.0.2.11', 53, 64496, 'EXAMPLE-NET-A', NULL, 'AT', NULL, 'info', 'AMBER', 'cmF3'),
 (now() - interval '3 hours', now(), 'Shadowserver Drone', 'Shadowserver', 'malicious-code', 'infected-system', 'mirai', '192.0.2.20', 23, 64496, 'EXAMPLE-NET-A', NULL, 'AT', 'mirai', 'high', 'AMBER', 'cmF3'),
 (now() - interval '1 day',   now(), 'Shadowserver Drone', 'Shadowserver', 'malicious-code', 'infected-system', 'mirai', '198.51.100.5', 23, 64497, 'EXAMPLE-NET-B', NULL, 'DE', 'mirai', 'high', 'AMBER', 'cmF3'),
 (now() - interval '2 days',  now(), 'Shadowserver Drone', 'Shadowserver', 'malicious-code', 'infected-system', 'emotet', '198.51.100.6', 443, 64497, 'EXAMPLE-NET-B', NULL, 'DE', 'emotet', 'high', 'AMBER', 'cmF3'),
 (now() - interval '3 days',  now(), 'Phishing URLs', 'ExampleFeeds', 'fraud', 'phishing', 'phishing', '203.0.113.7', 443, 64498, 'EXAMPLE-HOSTING', 'login.example.com', 'AT', NULL, 'medium', 'GREEN', 'cmF3'),
 (now() - interval '4 days',  now(), 'Phishing URLs', 'ExampleFeeds', 'fraud', 'phishing', 'phishing', '203.0.113.8', 443, 64498, 'EXAMPLE-HOSTING', 'bank.example.net', 'AT', NULL, 'medium', 'GREEN', 'cmF3'),
 (now() - interval '10 days', now(), 'Scanners', 'ExampleFeeds', 'information-gathering', 'scanner', 'ssh-scanner', '203.0.113.9', 22, 64499, 'EXAMPLE-ISP', NULL, 'FR', NULL, 'low', 'WHITE', 'cmF3'),
 (now() - interval '40 days', now(), 'Scanners', 'ExampleFeeds', 'information-gathering', 'scanner', 'ssh-scanner', '203.0.113.9', 22, 64499, 'EXAMPLE-ISP', NULL, 'FR', NULL, 'low', 'WHITE', 'cmF3');
