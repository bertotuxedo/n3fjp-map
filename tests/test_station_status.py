import asyncio
import unittest

from app.main import Hub, parse_station_status_payload


class StationStatusParserTests(unittest.TestCase):
    def test_parse_station_status_payload_xml(self):
        text = (
            "<CMD><STATIONSTATUS>"
            "<STATIONNAME>Run 1</STATIONNAME>"
            "<CALL>K1ABC</CALL>"
            "<BAND>20</BAND>"
            "<MODE>CW</MODE>"
            "<STATUS>RUN</STATUS>"
            "<GRID>FN31PR</GRID>"
            "<LAT>41.5</LAT>"
            "<LON>72.7</LON>"
            "</STATIONSTATUS></CMD>"
        )
        payload = parse_station_status_payload(text)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.get("station"), "Run 1")
        self.assertEqual(payload.get("call"), "K1ABC")
        self.assertEqual(payload.get("band"), "20")
        self.assertEqual(payload.get("mode"), "CW")
        self.assertEqual(payload.get("status"), "RUN")
        self.assertEqual(payload.get("grid"), "FN31PR")
        self.assertAlmostEqual(payload.get("lat"), 41.5)
        self.assertAlmostEqual(payload.get("lon"), -72.7)

    def test_parse_station_status_pipe_format(self):
        text = "Run 2|N1XYZ|40|PH|Idle|FN32qq"
        payload = parse_station_status_payload(text)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.get("station"), "Run 2")
        self.assertEqual(payload.get("operator"), "N1XYZ")
        self.assertEqual(payload.get("band"), "40")
        self.assertEqual(payload.get("mode"), "PH")
        self.assertEqual(payload.get("status"), "Idle")
        self.assertEqual(payload.get("grid"), "FN32QQ")


class StationPresenceTests(unittest.TestCase):
    def test_update_station_presence_deduplicates_sources(self):
        hub = Hub(
            initial_station_origins={
                "RUN 1": {"name": "Run 1", "lat": 41.5, "lon": -72.7, "grid": "FN31PR"}
            },
            primary_station_name="Run 1",
        )

        async def scenario():
            await hub.update_station_presence(
                "Run 1",
                meta={"band": "20", "mode": "PH"},
                source="api",
            )
            await hub.update_station_presence(
                "run 1",
                meta={"status": "RUN"},
                source="status",
            )

        asyncio.run(scenario())
        entries = hub.station_origin_entries()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        sources = sorted(entry.get("sources") or [])
        self.assertEqual(sources, ["api", "status"])
        self.assertEqual(entry.get("band"), "20")
        self.assertEqual(entry.get("mode"), "PH")
        self.assertEqual(entry.get("status"), "RUN")


if __name__ == "__main__":
    unittest.main()
