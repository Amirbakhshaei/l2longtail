===== Application Startup at 2026-07-17 14:33:34 =====

2026-07-17 16:33:48,960 [INFO] infra.flea_market_discovery: Loaded 63 whitelisted pools
2026-07-17 16:33:48,989 [INFO] process_b_sniper: Graph built: 18 nodes, 5 V2 + 58 V3 pools
2026-07-17 16:33:48,989 [INFO] __main__: Engine starting | mode=PAPER trade_size=$10 min_spread=0.5% pools=63
2026-07-17 16:33:49,477 [INFO] process_a_indexer: Process A: HTTP-poll Sync Engine started (queue=live)
2026-07-17 16:33:49,477 [INFO] infra.websocket_listener: LogsPoller started: interval=4.0s blocks=5 pools=63 v3=58
2026-07-17 16:33:49,478 [INFO] process_b_sniper: Process B: Graph Sniper started (WSS-driven)
2026-07-17 16:33:50,560 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.16
* Running on local URL:  http://0.0.0.0:7860, with SSR ⚡ (Node proxy -> Python :7861)
Exception ignored in: <function BaseEventLoop.__del__ at 0x7fa1c7c7be20>
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/asyncio/base_events.py", line 732, in __del__
    self.close()
  File "/usr/local/lib/python3.12/asyncio/unix_events.py", line 68, in close
    super().close()
  File "/usr/local/lib/python3.12/asyncio/selector_events.py", line 104, in close
    self._close_self_pipe()
  File "/usr/local/lib/python3.12/asyncio/selector_events.py", line 111, in _close_self_pipe
    self._remove_reader(self._ssock.fileno())
  File "/usr/local/lib/python3.12/asyncio/selector_events.py", line 298, in _remove_reader
    key = self._selector.get_key(fd)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/selectors.py", line 190, in get_key
    return mapping[fileobj]
           ~~~~~~~^^^^^^^^^
  File "/usr/local/lib/python3.12/selectors.py", line 71, in __getitem__
    fd = self._selector._fileobj_lookup(fileobj)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/selectors.py", line 225, in _fileobj_lookup
    return _fileobj_to_fd(fileobj)
           ^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/selectors.py", line 42, in _fileobj_to_fd
    raise ValueError("Invalid file descriptor: {}".format(fd))
ValueError: Invalid file descriptor: -1
Exception ignored in: <function BaseEventLoop.__del__ at 0x7fa1c7c7be20>
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/asyncio/base_events.py", line 732, in __del__
    self.close()
  File "/usr/local/lib/python3.12/asyncio/unix_events.py", line 68, in close
    super().close()
  File "/usr/local/lib/python3.12/asyncio/selector_events.py", line 104, in close
    self._close_self_pipe()
  File "/usr/local/lib/python3.12/asyncio/selector_events.py", line 111, in _close_self_pipe
    self._remove_reader(self._ssock.fileno())
  File "/usr/local/lib/python3.12/asyncio/selector_events.py", line 298, in _remove_reader
    key = self._selector.get_key(fd)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/selectors.py", line 190, in get_key
    return mapping[fileobj]
           ~~~~~~~^^^^^^^^^
  File "/usr/local/lib/python3.12/selectors.py", line 71, in __getitem__
    fd = self._selector._fileobj_lookup(fileobj)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/selectors.py", line 225, in _fileobj_lookup
    return _fileobj_to_fd(fileobj)
           ^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/selectors.py", line 42, in _fileobj_to_fd
    raise ValueError("Invalid file descriptor: {}".format(fd))
ValueError: Invalid file descriptor: -1
* To create a public link, set `share=True` in `launch()`.
2026-07-17 16:34:53,153 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1817.85
2026-07-17 16:35:56,504 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1817.85
2026-07-17 16:37:00,424 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.54
2026-07-17 16:38:02,685 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1821.77
2026-07-17 16:39:05,720 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1821.77
2026-07-17 16:40:08,616 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.37
2026-07-17 16:41:08,520 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.37
2026-07-17 16:42:11,528 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.03
2026-07-17 16:43:14,718 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.03
2026-07-17 16:44:17,654 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1821.30
2026-07-17 16:45:25,752 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.84
2026-07-17 16:46:28,677 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.62
2026-07-17 16:47:31,553 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.94
2026-07-17 16:48:34,105 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.74
2026-07-17 16:49:36,225 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.55
2026-07-17 16:50:38,527 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.55
2026-07-17 16:51:45,908 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.66
2026-07-17 16:52:48,591 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.00
2026-07-17 16:53:51,391 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.88
2026-07-17 16:54:54,746 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.69
2026-07-17 16:55:57,721 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.54
2026-07-17 16:57:00,678 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.19
2026-07-17 16:58:03,614 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1821.03
2026-07-17 16:59:10,606 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1822.00
2026-07-17 17:00:13,207 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1821.77
2026-07-17 17:01:16,261 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.62
2026-07-17 17:02:18,885 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.62
2026-07-17 17:03:26,330 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.30
2026-07-17 17:04:28,885 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.30
2026-07-17 17:05:31,937 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.05
2026-07-17 17:06:09,671 [INFO] process_a_indexer: SYNC ENGINE: 4850 events | last=0xf64dfe17 reserves=(12168797986707274419,22150448321)
2026-07-17 17:06:35,231 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.74
2026-07-17 17:07:38,018 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.74
2026-07-17 17:08:40,869 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1814.68
2026-07-17 17:09:43,978 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1814.68
2026-07-17 17:10:46,986 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1816.09
2026-07-17 17:11:50,141 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1816.09
2026-07-17 17:12:57,239 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1815.23
2026-07-17 17:14:00,542 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1814.17
2026-07-17 17:15:03,654 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1811.80
2026-07-17 17:16:06,530 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1811.80
2026-07-17 17:17:09,254 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1812.39
2026-07-17 17:18:11,829 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1812.67
2026-07-17 17:19:15,367 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1812.60
2026-07-17 17:20:18,181 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1813.25
2026-07-17 17:21:25,677 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1812.99
2026-07-17 17:22:28,642 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1812.74
2026-07-17 17:23:31,874 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1812.74
2026-07-17 17:24:34,953 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1815.73
2026-07-17 17:25:37,724 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1815.38
2026-07-17 17:26:41,338 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1815.52
2026-07-17 17:27:44,610 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1816.53
2026-07-17 17:28:52,424 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1817.34
2026-07-17 17:29:42,926 [INFO] process_a_indexer: SYNC ENGINE: 8850 events | last=0x3797927e reserves=(532366833189,2525207014103)
2026-07-17 17:29:56,044 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.29
2026-07-17 17:30:58,924 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.52
2026-07-17 17:32:02,543 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.38
2026-07-17 17:32:56,943 [INFO] process_a_indexer: SYNC ENGINE: 9800 events | last=0x3797927e reserves=(532494651631,2524600870890)
2026-07-17 17:33:05,436 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.46
2026-07-17 17:34:08,089 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1818.95
2026-07-17 17:35:08,501 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.54
2026-07-17 17:36:11,484 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1819.51
2026-07-17 17:37:14,976 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.62
2026-07-17 17:38:17,832 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1820.62
2026-07-17 17:39:20,819 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.47
2026-07-17 17:40:23,843 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.47
2026-07-17 17:41:26,811 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1824.06
2026-07-17 17:42:30,765 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1824.09
2026-07-17 17:43:33,729 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.11
2026-07-17 17:44:37,079 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.11
2026-07-17 17:45:39,704 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.11
2026-07-17 17:46:46,574 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1822.17
2026-07-17 17:48:02,654 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1822.17
2026-07-17 17:49:05,996 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1822.10
2026-07-17 17:49:51,886 [INFO] process_a_indexer: SYNC ENGINE: 11350 events | last=0x3797927e reserves=(532227423954,2525868455436)
2026-07-17 17:50:08,863 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1821.72
2026-07-17 17:51:11,473 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1821.72
2026-07-17 17:52:11,869 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1822.72
2026-07-17 17:53:22,988 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.21
2026-07-17 17:54:26,494 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1823.36
2026-07-17 17:55:34,663 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1824.22
2026-07-17 17:56:37,322 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1825.78
2026-07-17 17:57:40,459 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1826.85
2026-07-17 17:58:43,182 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1827.83
2026-07-17 17:59:50,441 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1828.79
2026-07-17 18:00:53,059 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1828.79
2026-07-17 18:01:55,872 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1829.29
2026-07-17 18:02:58,321 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1828.98
2026-07-17 18:04:00,976 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1829.59
2026-07-17 18:04:13,125 [INFO] process_a_indexer: SYNC ENGINE: 13250 events | last=0x3797927e reserves=(532274985295,2525642756934)
2026-07-17 18:05:03,305 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1828.37
2026-07-17 18:06:06,078 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1828.08
2026-07-17 18:07:08,670 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1828.08
2026-07-17 18:08:11,573 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1828.28
2026-07-17 18:09:14,472 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1828.28
2026-07-17 18:10:17,599 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1826.42
2026-07-17 18:11:20,149 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1826.42
2026-07-17 18:12:22,807 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.72
2026-07-17 18:13:26,292 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.72
2026-07-17 18:14:29,300 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.74
2026-07-17 18:15:32,102 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.74
2026-07-17 18:16:34,909 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.78
2026-07-17 18:17:37,743 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.78
2026-07-17 18:18:40,801 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1831.71
2026-07-17 18:19:43,535 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1831.71
2026-07-17 18:20:46,232 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1834.02
2026-07-17 18:21:49,813 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1834.48
2026-07-17 18:22:52,801 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.77
2026-07-17 18:23:55,873 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.77
2026-07-17 18:25:03,551 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.23
2026-07-17 18:26:06,581 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1829.95
2026-07-17 18:27:09,844 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.76
2026-07-17 18:28:12,871 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1830.84
2026-07-17 18:29:15,558 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1833.43
2026-07-17 18:30:18,486 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1833.96
2026-07-17 18:31:21,291 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1833.99
2026-07-17 18:32:23,745 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1834.04
2026-07-17 18:33:26,181 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.81
2026-07-17 18:34:33,171 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.81
2026-07-17 18:35:45,107 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1831.74
2026-07-17 18:36:48,031 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1831.74
2026-07-17 18:37:55,383 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.26
2026-07-17 18:38:58,451 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.42
2026-07-17 18:40:01,025 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.42
2026-07-17 18:41:03,880 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1833.17
2026-07-17 18:42:07,146 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1833.06
2026-07-17 18:43:09,991 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.98
2026-07-17 18:44:12,694 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1832.98
2026-07-17 18:45:16,051 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1834.91
2026-07-17 18:46:18,712 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1835.96
2026-07-17 18:47:21,681 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1836.09
2026-07-17 18:48:24,417 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1836.09
2026-07-17 18:49:27,620 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1835.51
2026-07-17 18:50:30,367 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1837.07
2026-07-17 18:51:32,926 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1837.74
2026-07-17 18:52:35,598 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1837.85
2026-07-17 18:53:38,521 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1837.83
2026-07-17 18:54:41,378 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1837.38
2026-07-17 18:55:44,855 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1837.38
2026-07-17 18:56:48,344 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1836.45
2026-07-17 18:56:52,287 [INFO] process_a_indexer: SYNC ENGINE: 19950 events | last=0x3797927e reserves=(533291384994,2520829136283)
2026-07-17 18:57:48,519 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1836.45
2026-07-17 18:58:55,942 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1835.17
2026-07-17 18:59:56,220 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1835.17
2026-07-17 19:00:59,540 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1834.50
2026-07-17 19:02:00,869 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1835.52
2026-07-17 19:03:03,808 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1836.40
2026-07-17 19:04:06,941 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1836.82
2026-07-17 19:05:18,528 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1838.51
2026-07-17 19:06:21,613 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1838.66
2026-07-17 19:07:25,030 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1838.70
2026-07-17 19:08:31,641 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1840.56
2026-07-17 19:09:34,576 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.29
2026-07-17 19:10:37,580 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1846.11
2026-07-17 19:11:40,180 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1847.45
2026-07-17 19:12:43,664 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1848.07
2026-07-17 19:13:50,882 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1849.64
2026-07-17 19:14:53,695 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1848.54
2026-07-17 19:16:00,977 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1848.54
2026-07-17 19:17:03,929 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.48
2026-07-17 19:18:07,089 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.76
2026-07-17 19:19:09,799 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.40
2026-07-17 19:20:13,116 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.40
2026-07-17 19:21:16,126 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1843.44
2026-07-17 19:22:18,644 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.60
2026-07-17 19:23:25,746 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1843.29
2026-07-17 19:24:28,773 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1844.01
2026-07-17 19:25:27,011 [INFO] process_a_indexer: SYNC ENGINE: 24650 events | last=0x3797927e reserves=(533033094038,2522050650313)
2026-07-17 19:25:31,309 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1844.01
2026-07-17 19:26:35,207 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1844.79
2026-07-17 19:27:39,055 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.30
2026-07-17 19:28:42,003 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.43
2026-07-17 19:29:54,041 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1846.56
2026-07-17 19:30:57,171 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.85
2026-07-17 19:32:00,024 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.34
2026-07-17 19:33:02,470 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.34
2026-07-17 19:34:06,426 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.63
2026-07-17 19:35:14,553 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.95
2026-07-17 19:36:17,126 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.95
2026-07-17 19:37:24,387 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1844.82
2026-07-17 19:38:27,474 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1843.57
2026-07-17 19:39:30,957 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1843.57
2026-07-17 19:40:34,436 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1843.79
2026-07-17 19:41:45,773 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1843.10
2026-07-17 19:42:48,544 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.69
2026-07-17 19:43:52,179 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.69
2026-07-17 19:45:04,686 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.88
2026-07-17 19:46:06,947 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.77
2026-07-17 19:47:09,742 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.77
2026-07-17 19:48:12,617 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1843.57
2026-07-17 19:49:15,696 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.22
2026-07-17 19:50:18,221 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.57
2026-07-17 19:51:21,350 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1845.44
2026-07-17 19:52:29,380 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1844.17
2026-07-17 19:53:32,306 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1844.17
2026-07-17 19:54:35,462 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1843.72
2026-07-17 19:55:42,376 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.73
2026-07-17 19:56:55,402 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1841.92
2026-07-17 19:58:02,561 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.16
2026-07-17 19:58:40,479 [INFO] process_a_indexer: SYNC ENGINE: 27550 events | last=0x3797927e reserves=(533138573832,2521551670613)
2026-07-17 19:59:05,742 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.16
2026-07-17 20:00:09,189 [INFO] process_a_indexer: SYNC ENGINE: 27600 events | last=0x3797927e reserves=(533124896638,2521616360430)
2026-07-17 20:00:09,308 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.59
2026-07-17 20:01:12,983 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.59
2026-07-17 20:02:20,635 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.26
2026-07-17 20:03:24,441 [INFO] infra.price_oracle: WETH oracle Tier 1 (GeckoTerminal): $1842.26
