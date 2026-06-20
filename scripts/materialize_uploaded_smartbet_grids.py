from __future__ import annotations

import base64
import zlib
from pathlib import Path

DATA = {
    'smartbet_correct_score_uploads_2026_06_20.csv': (
        'eNrFWE2L2zAQvRf6H/bYwsTow04cKAvZXraHZWFDzkax1aypYwVbId3++o5GyXYPvTgxHpJnpBD5PcujmSf17tiVtqiMt9BTG/qy'
        's7btX50vftaNhVe3t2BO5o1aRWvO3XOr9EfTFH3pOgunum1tV9D/DqW/9KvOnD72aXTo7131PpYG7Zxp+nj32Dx0bmu2dVP7OOJj'
        'H+8KpW2awr8d7OdPSqj5TMxnSsBzVfUHW9Xmbr03nX+w/u7fU0G9Nzv7JZNfk0O7g816BavNGjZt7W11t/Y4FT2sjr3vTFMbUDMB'
        '6RJUCmoOciZB4CdLlgT7G5+/EIV3hS52XV1NKUPCPNEEVhkKdJITWGVokMmcwCgjCFmihCW3DPzQdLDPh8IAzQisMjSoJCUwylAY'
        'HXkiCawyJAaoILDKUJhDNYFVRogNRWCUoTE20mRBYJUh8YUIAquMUFUEgVVGqCqKwCjjHoNjpnG9LgnRKXldbI/lL+snLLIoArVo'
        'DNGAaNMYdNyTDIFZNGDr/OtNMlSUsf7+DE+rF1iXzjemreDJda4sHT42ZokcVAbp4t0BjljVruDHkiYwbdKFR4HCKhbBw68xU0Zw'
        '8AcFCzQTi3EMxRX8ZPDyeOFRoDAZpQQefj1moRrMr6he80Wgou1fTuDhD+4tJfDwB9uWEzj4g18bccN5BT/+gNyKjT9YZkng4Q/W'
        'TBI4+M+e7BKCN3uy4SUwmrEFepCAm83YNTMwpgvTkf/hZQWPqx/w0Jk/dQOPpvY1aHSAGTlAiSVvJsiBjbiFHcg96sHbQG415qof'
        'yB3edk6Ymps8byj1MmN44zL67SxepmdXYzqNgdwhy0ZMzR0cllRh1hXDrAd/leMmNx/nPGYgdzj9yAjTc49aVwdxa9pVRUzPLXGF'
        '5YTpuUNWF4TpuUNWnxOm5j57qMsyu9lDDStn0T9djvdu9k9Dnxy/8O2/zukvFDfqlQ=='
    ),
    'smartbet_correct_score_uploads_2026_06_20_21_upcoming.csv': (
        'eNrNml1v2zYUhu/3K3K5AceCSH1ZQFGg8YzW3eYUcbJeCrTEOcJsyZDkZfn35Tm0sl4NZcmRg/0SEmD5ORJF8uUhx/4y1LJqxCRh'
        'pGMY60HKbnzqp+qP9ijhqT9JEM/ihY6qTlxPr0f1dBHHaqz7QcJz23VyqOh353qaz5tBPH99Tlfj+alvXq+liw69OI763/Xheej3'
        'Yt8e20lf8fW5+leo5fFYTS9n+QOPeb6I8wWP4a5pxrNsWnGzO4lhupXTzT83Be1JHOSPWfpTdO4OsF3/DLvPa9jK6UkOR9E1I+ye'
        'ZSM7gIwBT4DnwBYMYvXJooQk/1a3XcXV1FdJdRjaxgOdQR5xUgg6hyQqSCHoCbAoI/mnI79UT70M8uTpE0dLXYTg4zuXkULQ1WGU'
        'k/zTuar5ZcRIIehMvXM5KQSdq55uSQpBx3rX8k9PVL2nqqdLg/R2qq9TTz0nhaBjT5+SQtCxp09J/ulvVcUvEuxpY11oSzIl1f5S'
        '/ymn/36gU3QVRBJxkrZB/vBviR4rMmrfT0829EzT36/vYbX5Hd7L4SS6F9j81Q8vN6tejJPCq66d4/fu4cP6vvpw99u6+rzZktdK'
        'o5Jk/x7YRYKtMSWFjgTfCkYKHQm2Uk4KG4n2ZikpdCTk0xJdhI4Fx+6CFDoSHMM5KWwknN4UrdCRoK8rSKEj4f+Tvk37vZgUNpKE'
        'ZvmufK9dJC5n/HaRuPSEdpHgyKMVNpLZK3J0q1jYekWrAVn7Rq7m6Chb32j5XBx6yFxHsl49wurxHtb1RTT9AKvLIGrRA6Qlelee'
        'Kfsak2lcqma7dNJ0DdHMYabAEI2NsyR5R7ucrRmhKSHK1S1T4R3OkBvrwjscB+2S5B3tcpQ2Qmv75ioZZojGdl2SvKO5mgKnJO9o'
        'bNs5yTPabfrNEM1CPXA0We6mSoZoHLGXEPsfOK82Ko+0bE2U2fA1uybdo9m6JtP7Vl94EyvDYGuSCg1+eNzCx09beLh07dgK+CjO'
        'ogNQ/8JTtGvxdQ2zULVcOKlpIzANmUtdeEZzTMoUuvCMxtc6JnkFM6pnLc9g9aG1WjeLtUZoXKPNSJ7BicONAQZgNEXuMsJGYEyK'
        'a3kGoyFKSJ7BOFCkJK9gNEOxcp6xE/dpBGYON10YgbnDHI8RGG1QAd6HiTmXdF1SsTVBJgOUtkDz9MbWApndszMDtLw6r90n+GX3'
        'DnZn0XawE5emvXk3iL2KAPICODqQ10RRoW63cNKmzOkuO09zOr5kBSkEHZtYSvJPJxuKUzuWBuIzh6uV5nT+urMgBN1l52pKR6fE'
        'cPmCJUGevV7zc7X6eE53uXPAnB6y5hNaFMhJIejMYbLWnM4d5mvN6VjvGck/ffZUCeYwsbB1VcZD3eK6EqafgK21+o77d+avSs2+'
        'Xf8Km/sN3Mrjob2cYDOgq8s4ujr1nffIu0t4mHBxrTwn+eViz5qT/HJdrrx9O5eeNC7sUuGXTIklrgu/ZO5wH4YJ1+Ue6G/ncqfL'
        '6CZcl/vdTbjcYfrOhIttuCT55OodTq7mnyZcZGYkv1zso2OSXy7WLyP55F6dz9yUbH2PwaA0r6fpNKmt4zG643/Zc/QF8zNNSw=='
    ),
    'smartbet_correct_score_uploads_2026_06_20_updated.csv': (
        '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'
        'VL1NEOuY/2v/0t9yhe3o'
    ),
}

def main() -> int:
    out_dir = Path("inputs/smartbet_grids")
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, chunks in DATA.items():
        payload = "".join(chunks)
        content = zlib.decompress(base64.b64decode(payload))
        path = out_dir / filename
        path.write_bytes(content)
        print(f"Wrote {path} ({len(content)} bytes)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
