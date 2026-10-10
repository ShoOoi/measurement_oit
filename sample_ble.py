import asyncio
from bleak import BleakScanner

async def main():
    devices = await BleakScanner.discover(
        return_adv=True
    )

    for address, (device, adv) in devices.items():
        print(
            device.address,
            device.name,
            adv.rssi
        )

asyncio.run(main())