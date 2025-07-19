import asyncio
import blockcollector_random_3 as block_collector

my_custom_ranges = [
    (1000001, 1100000),
    (2400001, 2800000),
]

if __name__ == "__main__":
    asyncio.run(block_collector.main(custom_ranges=my_custom_ranges))

