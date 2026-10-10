import asyncio
from bleak import BleakScanner

def detection_callback(device, adv):
    print("=" * 60)
    print("BLE Address :", device.address)
    print("Device Name :", device.name)
    print("Local Name  :", adv.local_name)
    print("RSSI        :", adv.rssi)
    print("Manufacturer:", {
        k: v.hex() for k, v in adv.manufacturer_data.items()
    })
    print("Service Data:", {
        k: v.hex() for k, v in adv.service_data.items()
    })
    print("Service UUID:", adv.service_uuids)

async def main():
    print("BLEスキャン開始（15秒間）")

    async with BleakScanner(
        detection_callback=detection_callback
    ):
        await asyncio.sleep(15)

    print("スキャン終了")

if __name__ == "__main__":
    asyncio.run(main())