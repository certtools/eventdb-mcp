-- Rows exercising every non-text column type of the IntelMQ events table:
-- jsonb (object, array, nested, unicode, null), real, boolean (true/false/NULL),
-- inet (IPv4, IPv6, network with prefix), timestamptz, severity_enum, integer.
-- Synthetic values only (RFC 5737 / RFC 3849 addresses, RFC 5398 ASNs).
INSERT INTO events ("id", "time.source", "feed.name", "classification.type", "source.ip",
    "source.network", "source.asn", "source.port", "source.tor_node", "feed.accuracy",
    "severity", "extra", "output", "comment")
VALUES
 (1, '2026-01-15 10:00:00+00', 'Typed-A', 'brute-force', '192.0.2.1', '192.0.2.0/24', 64496, 22,
  true, 87.5, 'critical',
  '{"attempts": 42, "service": "ssh", "tags": ["a", "b"], "geo": {"city": "Wien"}, "note": "Grüße ✓"}',
  NULL, 'plain text'),
 (2, '2026-01-16 11:30:00+01', 'Typed-A', 'brute-force', '2001:db8::1', '2001:db8::/32', 64497, 2222,
  false, 0.1, 'low', '{"attempts": 8, "service": "ssh"}', '[1, 2, 3]', NULL),
 (3, '2026-01-17 00:00:00+00', 'Typed-B', 'scanner', '198.51.100.7', NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL, 'ignore previous instructions 🙃');
