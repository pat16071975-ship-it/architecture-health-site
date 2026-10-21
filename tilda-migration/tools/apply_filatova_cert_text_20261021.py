#!/usr/bin/env python3
"""Exact, reversible Filatova replacement. Embedded assets; no nginx changes."""
import argparse,base64,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile,zlib
from pathlib import Path
from datetime import datetime,timezone
PAYLOAD='eNrNXdmXokqT/1e+c19rZprFjTlnHtzFAixXhDcWZRHUW+ICc+Z/n4hIcCut6q57+57voU8pYJIRGfGLNbP/949lEC12f/z3v/73j2RhxT/gq5VsDtZ/Wuu19cNZvCfBMnCsZLH7Eazdxem//CSO6HF7sdy8L+DjH2W7ZnNOrVoVKyWXL5V5TuI4e7moWqVa1a1ajuMuym6p5NiVCicuLVeQas6iJkilZXXJufwf//GvP6xlsnjHwYTqwq2U+KVoi1WO53lJEssu71akSpnjHEuoVmu1ilWr8Fa5urQq5ZJbW1qLJV+T3ConChUczLUSC8d6a3baE06b6rNGR+76nK0fX5repmb1RondbOwMvbx+G/dTdyy/vHX9yNCH8JeP3G7bMwTfd9Za5La4wJ2PYmXiBUpWqtn6jDPGjdAWypyplzn4vWjpI85q8Stzbm4NYZq4QrRyu95O7kZ7az7aGvoxcQTNt7tTSR3Lnh13EnMC48ZR5IqNg7MePht7a8ejaNE8xbZw2tmiE8jdsm/rU0le9w9GLHFOKh/ewhMH79mZk+pwqzVLnHIY7rRuk1Oqw13SPMpac8gp4vDg0ucpp2TDPd43WvC3yXOKwu4NXutRszXyXLhmwN8ExjIm+EyFU/B7V4Hfv+T3XjhlMjwkTYkzlOGu2RrugtZw7eJ1GC/pNjglHO5cHIPGhDGmNUUcbd3uKYI5J+Z85Mtd7WCvR5G9Hkpy0vQ5ZT7yaO7hyCda3hktxs7jjF7+3uxM317C72/wfhHfWce5wJw4vHdw8Zm6x70SDVVGQzG/ZvnCF6LrCV+6ygDo2ifdFtExbnRms/7bmOsMp+22xymeR3x28f5ymLjAD6A7Gbeaf0rNJY4F82oTXS7jCfFMgTGlrorvB1re4P10LdG6I06xcT1eGL1dFe6lz/gO8ywd33d7uG/huu819v49rrv6+s71W8jPDvITfgs0veOYSAutWSIRr+AZ4ssohLnAeDsYz83lB+eB455oDA1p2OzloLE3dD4CGQW510Injo5uNzrYAcnjztLL76Bzqakbkhz7nNtrZIOgdjDm28TUR3tH2O2d9RTYO/PtsRRb+sl3u5JgjHnfjsu+ImiRsx5tzTgKjfkoctJaIPf6kQ36aAidPegI6FpnJwc10NkT6MnOs+C+GXKBInQyR5iBbkihmqmiGsupIQw5reVx6qQRaa0hr6blzI2d85iWrj2eeyD5jgg40FNh7pvE7kq+CXptplyqtmRBndRTFeaPOuukXMnI2qkmmL4xUU/GxMjUcBYrsZaBnnpOPNsBvzK3F+2AZxsT9P3h/EUNdGS6N0QtexMNadCF+eujWNXlbDCZhcB7mNsRMEDjFvopcgQ/Muc5hiDmpI0NvCt+G8sHu9sp2yLyohw6ovrDDTlBi4fioKWWB612SYXRCh444qhsd2eZ1Z1FbpN4kRlif+v0hp7ZncXmXAZ+AB1iP8SxHSHizHH5T6Dt5a0pFc9eYai0h/XdI4Y+0kGUvxXJq0ifI61ZY3IKeow65DbT/F7g9Yeo/3CN6TjoabuQc/g+CtLWc/1XZI/rIz7geFXEIsAVlGl8vodj8MDToxNLR3Pe5xaAzbbgVECeOcDsJ9isDDef4iToCZtL7ULzAb9nMKeE6RzDsIR0T8RnEJ9XRJdEdBVzHu7HZ14RrU94NQqyjUc6LiFtbW80m40nXNKYdhoNGLtEOKL08H4PMAlxBngx9Y5/HoZr/A3My0fsQ+yhe1WyDTB3wAbCqFGIvKZrk9HKbQqEoQnROwpzO/B4LYAu9bWqwP04QVkI8/e/4xojXg6Rnz7ZDAVpquCYRAut45LxijAV6DWyUViD8Zqf2x0FZbbXPzjdWer2VoBH0noAdhVscmrNG5ylS3umhx/l1hVmAZPzB2OsR2XnqXz0N25vdHSyzQHwqAL3V/Zae1fWfR6WCXwN7gDYtgNsO7hxOwFfZG+MpRDlz9LRj+hw5lw9PJTLeZ/hXi/H3WaDW8wbiMW+02sAfmk+w2JpZzaTPx2gRw7LVXmVz2/uc3K48a5xGXgBeMT7SiylZirv5Ga/Djh1BLor8Hlu6v2hobuRHBw9GGdrNun630Oj6EbGuuGbAvCyWQ+Ivrv5/RW7gWOSz0W0KP/uPhLNF/A2x9PR1hZKwSCoA4Z+in/oXxA9B+2MHVWiD7BEcZ/P6QE/4JkVYCbpuUS+V3+C45ZIH+n5OT6H9yoy0m68kW8gt2ez6UpShrPGcAiYtW+Srp7wvi8hH0XiJ/NNfjC/A/iXoG/E/IwX5hPB3I2M4YxR8/JrHGcCD8h/qjJ6jSPD98d4CvMMm/u0BPN/R0wt5e+vIJ4g5q3IBszZWgNN4Oc1GS0kByLjFeEi0Av+mvEDxjt+bk+CEq0h+BEqxhiu3lnnegSxBj+ejUm/tg7fgBhj9GYGcK+3C4bdaPWV3DNdIf8gWI5BF2Peh/d0wIfeAi6BnDRQzwkPaKyoAfpAvg2+k3w2up48sjP1y5rT5ydrrtQZfbEUzHIbSfLZp+dgPCd57qurj20fmx/vxEcaC2I0zumpFSWV/EXMR2a3nFlpGfgnAb52EkUwt+DbcLZo+ooO/mDcZtghSKklal1blAtefI1VqRaS36SrwXLOS9e+TPPKB7J0iFHiTkXR++B/dVZwnbd1OSmwE7D6d+NwE/w68D+11NX5YNKNMvDTSJYADxP63p7t4PcYTyH99iK9+b1qzTVuDLhoMxk82uRrkt9ekVvtexmZMFzI/TCMUxD/Y7CHOvfFOtVIPn/t/R9k1DrjGbw/QZ386ffncpL2JaV5pxOr0xZ83T7yjMZbN8iXJT8A7qspvoPi7Bx3/4rvelmfb9u0b9AArLqj4TPb8TfM8YHdXc5udQn8FYpXZMA51JFFwF+Pw+KisfzSDMr399AHwu/RQhgB3Q3wKVYVE8Y1W8kafKCVgjI1x+ujSMFYrjdL7LUKeh7tbb1Tem3Vd2rmHZ2et5W7PMRbvL9oekel1YEocFU1hc5xEA7xftXpdlYsxuMBX+oVddI/LlrJEWKwFWDtGvT/AFhysCebTF03Ssvxx/laXYmz08bW1t0qjJU53ZO/mGwDiB9DK0vynNHmpLbq0QDiN0t3N25r67vgKz0fr7431jMOru1v+BADX1r3Y255kI+tuV4lF392G6JvCnFcFXiSmkK0B74dHPiNBnQMRPDBuuBvxA7Q7B8XzfpJ7cF1Afxe9JcDHuPQrTtX4f7ouOhxe4yJr3084E8MdG/UeUIxsTLXtot4WlEzE3mIsTWsT0Hv5qSE0/NcBmHdU/GdOBfBPACGJeB/b03B5wahd1QfvA/kAmLpBPD0dHDCTaiFvq8K/dXH3xtHtZUkxry/tvRSBd5zlFsqrLcvKYTp1/YE1j6uQcx7uppb45Pnji+wxsKvyqY6MVDevBsZ1c30dZKsjTnIXdhAnhVzTjDfB2ufDEI5/XTevVEV1nwHNrAiZx4/aE1L2qRYD7fgvaC2PqNpdL0uVbSP5pgH2xStrd6woo5LvDZn62EIEBN1o5jZu1n2iA9Ez+SGHkYfrEMu73dxAZvvIAa7DDGXNR9WYewSuN0+zGVth1uwcyY3EICfXSkdBGqqttzYyOpVI+6EluCmtjjbm61NaIb9eNA1w7Mch5sT8h3sMcjj0JMFtWxOGpEZJoEtYj4LbHrcWVnzWTYI/RvdH4RtDn+rTkieQ4hVMuSz05W2mMd0wiTBtYI553I3LCGmuIKPPlsCfPENYVqxwb4Df3cDQRKA7hhoEGFtUuQT+IxleD4CW5+vzy22yj0N5lN+hCu3OjfRaI5n3ZhM+YHA+07sbu2AYZs2P+PXLf8n2w3IA7w3CQxde4d1PLh6eTUItEjTjdSM+w/nNojNgyHOMmVubsEewbymu8tzQ5AVmAvQb8YS8E5N3DjKLL0PuH+8yFKTr9gCyPmHsWZVzGWRLAL23qxhJh81wQxBxw+AoTnOSLFJOXx89oG+zBspyCzYFIYV8I4KjJtqvUf4Q/jxCL8uPA3Zb+UW4FiPe5WFj/xRdKwHnA6mWOD1FuVlo07aR8RapYt1AhW+14/u2vGUcZ3GfB2vUB9LICNb0O/NIC4f7HjKrp3nsw1h7PC1BWN1R4LVrCfgU6G8bm+wvXWW8+vPVzLfTlFmaf1jE+sOFQtsNsSk++VY+ygrYx50o7PD3MQg7oN8JyvMWSwE+gwYKPF2PKwAjwJjjDYXcK553GtpJF3LBmAb6DL4zusE5qEdMXcJMgD47lWBLlgH0CvCUrUCcoI2LR0Iro/2i+zUjf1qMPtFtuR2HrkuAk6Ajw1jXNnzO1wYrcwedy2XHMjlDvAllbteCnKDc4N3MZ3P6z7I0zs9NG9x73ZMiD2k7JH8F+MDVoC8f8RD0I0U5poOwPezcrte0DQIO0B/IwPdh/inHhqCFt7zQRbM+DHmsfle06S1RudrV9jGXfTqWr9nVdDvPdCRWfNtNAhV8qs+2JA595HmqzUc9G6xCXHAnLs/IfPtFOUB7Cj4CDPAqtPBzZhvleMt+qQga0ewqbONAeMihjM/qQN6nAA+zEILbI0hUv4F8L0DvhLIBv1dwd9obwhesohBZm/pECx9JoKPm4COX8lxH/RDex+Iuf6GMN/mtHrmZdzh3W7tgY7PbnUEa4Yp8YT4ZeoYl/LrxWVctOvom70OuxgPRf5rF31dvvAPy9okAtmIqo/w0Ka4GtYP7TbIO8SSOH/UcbQjEO+Ws0F87T+D9SR8aqDvBzLubUC3T+Za3ua6Sfj1wKeFMcwD6NHpHn8Hnz4/2r3F4IOuv/CtwI/K38/9CvYjLcv5U94JavYTvLvYZbDrM7QlqMcJXE9NXaMYbRDncw2vfL1H/m3Xh5hLupWLUC0xvLuVpbO/RrIkPfXtrFbnTj/l0kP9HJeyB+sAdGIcdvYjAD9GgIvcDQ33eJljWeKKgO+trQhYc7zVbcBf4RQYcxnm169STNPk4bu2dbIrHW9O0SYK6Legfuf+3O1YugY2C9f2jIU3OsR09vzb9JsYQ2vwOuelZng8sNrZ9BzjvgXXY2Jue/YgrjSfxZWc+gH7KD/+YO1U4cnapdo1/qU88RxkY7+YuxsYM3o0vtO7t13+k7jN4G957oI+ja5ln/QTdBCuo22fJXgdfWCMTVyd38O6pua8ERnz4UYNTh/w4xIHjW7tZ9b/EDeAbCNWPbV/Zhf81u7MfxzL9u98fPKZflWPb32AWz1guRXmMzzwNbH+O72yq+29mk3vY/R7fdlgDvA61jJgnQ1h9yD+MTH+8a/inyPETTzmhZ1we7ThHeRL3fhP/VueTxjPQe4FFs/kdIKf2r+1f5wF/DCCX819AO63KD48GPE2MsQh4iBH/F2PMMd9o8PkB05Kj+LPO77d5FRuePet+KLH1zF3bOntzS0mG9kAMWm+u+YF6cDiUx3YoKzn+Sf5eO9PMX/3Jv4U7p8BXFmb4wc5F4xdb+XwSDW8XuMOQz6OSe/FMdf3Y/Y/YPz1el9kWM5yW1pTxBwTASfhvm+2SjXQ09ViUqrRd5A9uauBDGsZ9kdYZMuxHwv1FHuz2h7I4RZsz87QWa/Cd/pa8tod1sMwl5vXMrCeLXvgt/qOqEoy6JAp1IJL/0cteAtBP3XHAxtAdf9frxlh74qWGtmv9aZgXVkR2kB/nye7EDTA5kY++TnrEdph5FdigF1X9LLvBnLBp2jRa/jYO2YCr8y4s4OYO7jiMfiNZR71wxRcyrly/Yz1Lhn4zvx9Yalmxx1B7nZAftsJ+BEB9Z0kzQXWuQ5FHZP+Tj2s9eRjBHmO93puQMcoIv+6p17xG+/xFC+aXUm08bmeBn4m+Bfr1QvW2XZa8wetK9UhDkWPFfUh7O/6CPYttMWAx4ABKF+Yl4P186/6eXgH/FhTWB3M9WzP/Puyb3ekFeZGsT4B7w3esD5mvOG4Fc6wn9clP+lJA/l4e2tK/rP3g48CNks6gq+3XehtfOciH4+nukdem/tyHD06wBpnhhD5dhdwDtYK8dyYN0AW2Nru8zrrjvpSiJ77ng72XsbnH8Tf7HgwgIfG+NJP5M41wC13q6BvLaIMsjgcc0yAuWX2vrf8PUpe3xXz/o95ro9FH8xX7yiDHII8CxHm+NbWPPLlsHrbO0I9d1hHf7/00aEusjrMyE9YD8v1muR9MFne/8LnfXRYE871rX3VizXLwL6vrbF0hPkJTEZMwD/UQ4oRQsBlmhfrn6EaKX+pPytXNWzSD+4yL5Cn1mP6F0h/irxUqNeg6A880DsIu6rn/h2jxubdlCDeNOgv9laCz57dyQzVxHIe5jKL407O8wAs5kBnd07aCEHfMwcwCzFA0WGtxcbKFt29DLhw0TGg+0E/TzL5Ug8Pc9A7h2gseaS7N/X56ce1ECRuEUcC8RoxV1mOAgn7gHYP1q2PfXsgl9rGEki3PKrfUZ8B9Sf1SGZyuhXwo9yQ/hYYdffu5IBxpqkn4C8UMoFzf897vH7g/JkdIgw8YA8o9i41UM88CeXNe0AT828Ah7LLurBesNymKQ90nzADZZHJHcqXnfepiGQHH/wGbKeMfLBJflg/SIFZrL5/kSOQbwPxeusGFzkobHVuf/I5X8tuH+TrWHmFpwctFfy5Yaq15BM8866l9Y2aednruI7XEzXEf232Pspnl4CXP4aHsYh61GS2un4cgN+r4mcYZxDWj0rrFDhB7QXrmTt4dpV0G+dnJ7fPYu0R8zXod1zk72wDsa8b4tOSd6GrDzHo7sr2awHEr+/odyGfI+QzrjP1wFKvj4p4XfTDybmd2lMP77Hg5QZ7Bcw52j/s1SjBX7R5hS0/+Qb1hSpIdyvvoct7W1q5fiJedBX5ai3OuaoYfafrfoT+3ZrIB5z76t7nYbjrKWn9FnuovltDOflQeyeZCuoHufURUx70Y9B7/0qv1dX7aB0LP4TynjHrz2O+r/zyaX07QywCf4j0G3gfll5B5qI8t3fli5xjDfA7L7184Ptenr33pWFMq9V++TftzQJ6vRPI/fHtr/X7eKVNc/pZT+tjGuSf6r3F/k1mM7GfFHT/Vo88lIck702F34P/dRj5eW9ZojH7DXa1nPfJV+764Pm/0AePffXeBS8V1Ef+TBf1/ed9IbS+uBcgxDk+9VeZ/Q7z/QQ9ejfQMfI1+I62L+l2H+0RyPcrPN0DgDwsxj7kdiCU8rkaE7Q/EtJc+EM3Pb3MVuV+3w+UtXrRy0hr4zK/Zk/+oZywPj0mC0BbjWQQaQc/j+RLWed+VQtkH3ROrAPG15kv/xDbyL8hnpF/xHzgI647s6lirkOf9lz++Jm9FszvLD3as0Hrs0sY7T7qJeb1Wsx3ubIbdzmvYfDmbWoG1cM0iKWuMPo+/0NY8RlWXeIM3HdCev6klxvtiop+WKv6vA89+Mv4wfoqg3rNxXrGuLHCPDt9nnCBGtZTLeCOWpPL1Kb8omZ17+KjzpHnb3JLPqqh4aHvzXrB67QHyNanLwV/i56Wx3GmgjpRR51ne2FwXR/vEbrtPb2O6a9rjD727IGNH/lut83WB2PkOcYwCfbtXffxFT7ChxzA7+Q5w9pnPbVH7yf5DDSeDpjjtGMH8xr+AuLaB3mIq9z+zZ6WyJ33D5bQOTrd9t5aN9bUc96UCp5c5yU+yvw5Hzf9dV7/lXWHuKOQJ/T7zno5AZsP8gXzgrlOH8oGy3mhn+alMId/ZO/WT+pxvl+jsDM5VgUPe6vRFiBWvpxzE8YPxDWZ4kXC7Zr31iMd9Eys7c3psySHJEMJrH8CcgAyz2wcy2/9YHYAfsv0nvJoL0XsyDCh9OW+MaD3CLp09r0/5vWqOOcK2Q4p7+9+uu8HfYt/oofcCx7if55HBhlGXPRzOcK9IakNMdK/CY78I3KsNOvsPvOr3miPkfZbcOngxHxop+U/nW6RI73kKy+25ON6XfpVZJJ7kDu6Ds/kY8oe9SqDbTuPc841vCM9yiU+e7Ju4VP8KXIMrzm+zBmG4+97tG8qj4cq+X6pBuYfaX3I7yS/cBQGf22f2wVvKEa84HZBb3AfCz7qRy5yNkW//5L8xz3zoy8xMukV64/OfVPcL1ZjuQlm+xXxHFu93MRkd7gAcwrVcFgaTBxBy+RUvbHvmm+vTcwl5T36IFO6xIMrmWotr6xlhqiGlIe49+UEa67xhn70sA/N1Ts77CdY9IZF/rq4n9y+mztp8F1tqak2WaVatvpsbKxr7iH+D57kv7y8dsziT+xlvPT4kC18FNMUPGR5jXN+A/N/F76sza0jwho2G+e5sB7KnE/n+7d8UiZtAb4f1UwWBxNVBBqD52NjX0b5XKd5ln+Ti7r4uJ/3I27hd9jjGFEOa0NyhHsO8z2I0lU+9iqvaT3ISxVrJ/fOc2J7K4LGFusf57WdyNygtRK10EnVsA12zoDv9UwNnbLWWvEq2f7HY2PvBOVa7nIrtjiqwJy+pO9hvvK302Wc1HAF/vmv03WVg/5KPt1fyUv/tvULQTe/SSerNZi8HbOaPtjmJfY6urifptcv/wQP/Od7fS81jqscM/3927EoW5WAL/w3sOhcS7rUjWpf6+yTetE/QGdZnajpr9N5lc8NGmy/yYRyqdQrD9exL2YL+Fjk9SXCJIn8SJaToBi9qMf9dvwFn2cK95xv4G8D957RPrAvselp/eC30wfrWRfVbPpX6IO5SWtbNA9O8KWeTvOca7H/ts583noe56DvsizyUuTTHKTzvi+pyDuy/Br6fHmuLq8/wveOrLG83h5z+Yr6D+Ef8HIyzLTg1/HPAT/Y0gHrmljfBH6O+XNtyRaNn5EdiglClj8d7oNbXw9tuLGWD8D7H3n9N6X4HffkslxjN98vJxV56X9Ot1b8oKV+Q/Zob/uXvHlWY/3d+AgykWJf1jfsANU8DF3jfsIvtS6xRyvPEez+OXnP6qfv2HvMLX6N/bTn9ZTXA/J6CZPp79s4bWPg3s2fplEF300uaT/pk/52mzvx4LNc/imZwvxdH/sCcN+piHrgY203cpse90o5j4DuN2WGDeEJ/ayDHTsveH0lwXN9roL12+L68c/6CfUVbKGgtgz45xzfuv0U43/Wh1Jew3sNjH/Xkw31nDm4byKrNjetKQe/QVsDf1cP3vmOOLYcsD5eNtbu1dMmw6MW1rFP8fQNmc6sbgf3PX+FR+KgNRS0SZ1Dv0abOEd4H/xrB3f16g98vu4HwD5XzG3iPlrMYzKZaP8GHXRKWsvg1dY0HUzq5QHNtf6tGMPpSjs6H01oJ+czVj7HnGXey1O66tkpajn16zod2PIS1YRYzrjxoOZFtjzJ69tUbzGm38WvX1nrkjrxODUDOaY9Zcg/+ZtrTecu+b/BRj6Z53dsZQPWEfela/7XuPsjr4uHed71ndVH854YlvOf5L7CfQ9btajB/mNrqE1QDwreTH9lDUMWh+Bvbu3J34bbmcfh+V5aa4rYIrI51oXv+QRsrl/5O1uN1X7B91XoXD8h2Zrj+obVMFFXve13fTsHz2OkWtLXdGvhFHAbaM9UIaebz7HnOTYFD31iPMMDYgz1t67Tg/l+Z53OfrsC+ID7I3/Ch7OL+jv1+FD/nfegF++JToF9LvZBIH9sHffO0bx97NfMab/qofq01jvP/chLze9KX4sxi709D2sru8H8tj55q/NGLB0sgfbfhMa8v2L1eBVwZ3nOYQRacfbYbf66wCAPewYIc8jvZf0U1DPtZXmfzRj7KXasPpD3iE89zOWzmlLvql+a8ufA+4z1hlBNhnxPWETOy/t1cjtWxfVJczvG5jhmNcC7HuiRV/Rc0FlmRY68qNXKZ2y+OjvlvEYKnaFC+1zovI83cZbhuX/mpBEClqRGbIhGNguMDDBRmJaM0A/BFyupcT80wrZgCHJs6PxxINLZaBLE5iGsd+qkpm/P61VHwPNpsO95tCzuvU3aGfi9ELN457MIbVgrpztjuNkb4V4zOot1LvTx3Nb3b/fjXfNk3diArxdddP+b/YvXesH2QcN7Opmlgx/2Uc5XJOfUx7TDs+ZQJqO73j66puh9/2xTsX/mVczruTLVRElOqBcp7zki3wjreUvE26L3J5enwyjAnl6G5fu7Hr6LPcp9rETL9y9Qb8yc5UKCRit/rqD7tpb7QdbO70sgpgzNeYN7Uu8/yLmPtivyLag/CndT28PaIPKU7AXj822dqHsC2e2X72q0dN5s4dc+6E9IzngVftLrc9mfiLi7Ue/Hw30weAbvp72IMuJE+qD+jb3lWPdEvAT9OWXmk/4V2rs4flyn3BA+zHP/eJn3VB6f1Ed/An8/67kIHvY55XvZH86vFWW3tdGHtv/DXmf0J6PETBu+3RvS+aOXc7jyvg2dZ2cHZrdxMJ2zgPFhfs7jW+BtXifcy4J6rjVO7g4ZPrHzsvamMONG4N/YaPvb/fJYH276n53hkDpbBeb2po9wH1lCthdx6nwG16i5mEar16Z7vU8xwb7Rfrra5eeL/sIcUKzoN+vX8Yn2mb/pw70zn8G9aGLC7wxxdHACb/1xzzjMtbcFGeYks1k+ufosXczAt+nOCINfm86HvfgwRzxHZu+IQ1Yvn/DV696dQbRb35wv+sXZiFe9h9RziGeaXs5Vdnb99HLW7vUeaOo3wT3OYftw/X4l3h7N1HWVLuu3wPn8Q/0ga6XpHBAjzPkou+l9nlC/wwliupMisHkV81xOErCrQzxHNII1wdrT2+V8Am7dz5KVLeDeJ/Q9P67RUD/tQJbs696F+RjWNTYPzmrmG4K3MULuxQjKuI+z42KPdJvOCYvQx4Xf4jl3oZU6MM5Kelvj2TDlzG02qD8R5BjwsGPD76mfBfswlJhk3x3k53Yra/RrR63zOads362tdriqpXN71tey/J19LRW56bxTr9Zsd5xPkq09LmdOrEr5tRNcI92AuROOs3PyymDreJCnKdOnOeoC+DpNZzsQSS/2FBu3L+eR96VmSL3HLotJfZZ3GIVNp8pwuIzr3wBsBSwFjO3S2ISruMYoyzD+ri+A79PTUOerj9eev173y+fLWX773I/fX53JUr3BjfPnUQfkhWFRfj60El/OGHobuxuwoStTL60HAowr+AeX4wGLOygT1fx8ifx8jNPUFuuS2sr5ucbzZiFub4OdBf7A9ZWy7vAwdnma+3UQ02/66ce9+ED7Pj/zZ/M653Bv9y/9Fve/juYm0kdnCJrUo++tDYp1dqCPEFtOyitcc/wN0LJF+j75HZ5DVPzue7ycN1KTzg6ZZZPzurgS0RZ3Vmab7Rcj+WO+LMiHhjHlez+FOBrea+nGxhyDGzfvrwEbpLcJv3qdn/UyRXxHmzCkM1uwZ8pt2VT3Go2RZ7hm+Rhhft1468l7mHfs9k61m+treQ+x+hr8/toSzxfHvfEwP31ySkGvD87t8yY+X5w59grzyHm2wTMPgEayEV/TebyzuWD76YwFZ89svkbrgu9ne1FhbcAOkX7psxXqTK5rlJ8cns+y48Gfqa251zdWj2O9joV+Vv4u3Z2KeL+8foV5v44jiWQXeH5zrmET46dRmfY34/8t0Sr9zx//93//8a8/tu9AiZMs3Gf/w8bNf6rxB7eU+MpSEIVyeblcOq5QwWM1LJGzFpxQs8WqXeMl0aotK3zVrS6Xkl1Z1OyK7fA1seos6H/S2AXAO2v7Xyc2ZK1S5VypUipXKuKytJSsml21bHfpchJcdnBwu7Z03apUkSRgjc1XXHEplEucaFd4oQx0/D/cih4G'
M=json.loads(zlib.decompress(base64.b64decode(PAYLOAD)))
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
BACKUPS=Path('/var/backups/azmednsk')
def sha(data):return hashlib.sha256(data).hexdigest() if data is not None else None
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def safe(root,n):
 p=root/n
 require(p.resolve().is_relative_to(root.resolve()),'Unsafe path '+n)
 require(not any(q.is_symlink() for q in [p,*p.parents]),'Symlink '+n)
 return p
def read(p):return p.read_bytes() if p.exists() else None
def prepare(root):
 result={}
 for n,r in M['files'].items():
  p=safe(root,n);old=read(p);new=base64.b64decode(r['data']) if r['data'] is not None else None
  require(sha(new)==r['after'],'Payload mismatch '+n)
  require(sha(old) in (r['before'],r['after']),'Изменён исходный файл: '+n)
  result[n]=(old,new,p.stat() if old is not None else None)
 for n,h in M['protected'].items():require(sha(safe(root,n).read_bytes())==h,'Protected file changed: '+n)
 return result
def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-filatova-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  owner=st or p.parent.stat();os.chown(tmp,owner.st_uid,owner.st_gid);os.chmod(tmp,(st.st_mode&0o7777) if st else 0o644);os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def verify_http(n,expected):
 path=n.removesuffix('index.html') if n.endswith('index.html') else n
 with tempfile.TemporaryDirectory() as td:
  body=Path(td)/'body';headers=Path(td)/'headers'
  r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','25','-sS','-w','%{http_code}','-D',str(headers),'-o',str(body),'-H','Cache-Control: no-cache','https://azmednsk.ru/'+path],capture_output=True)
  require(r.returncode==0,'HTTPS request failed '+n)
  if expected is None:
   require(r.stdout.strip() in (b'404',b'410'),'Removed profile must return404/410: '+n);return
  require(r.stdout.strip()==b'200' and body.read_bytes()==expected,'HTTPS content mismatch '+n)
  if n.endswith('.jpg'):require('content-type: image/jpeg' in headers.read_text().lower(),'JPEG MIME mismatch '+n)
def verify_all(prepared,verify):
 for n,(_,new,_) in prepared.items():verify(n,new)
def apply(root,backups,verify=verify_http):
 prepared=prepare(root);changes={n:r for n,r in prepared.items() if r[0]!=r[1]}
 if not changes:verify_all(prepared,verify);print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
 backup=backups/('filatova-cert-text-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
 for n,(old,_,st) in changes.items():
  if old is not None:
   p=backup/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,p);require(p.read_bytes()==old,'Backup mismatch '+n)
 (backup/'manifest.json').write_text(json.dumps({n:{'before':r['before'],'after':r['after']} for n,r in M['files'].items()},indent=2))
 created=[];written=[];removed_dirs={}
 try:
  prepare(root)
  for n,(old,new,st) in changes.items():
   p=safe(root,n);require(read(p)==old,'Concurrent change '+n)
   if new is None:
    parent_st=p.parent.stat();p.unlink();written.append(n);p.parent.rmdir();removed_dirs[n]=parent_st;continue
   missing=[];parent=p.parent
   while not parent.exists():missing.append(parent);parent=parent.parent
   for d in reversed(missing):
    owner=d.parent.stat();d.mkdir(mode=0o755);os.chown(d,owner.st_uid,owner.st_gid);created.append(d)
   atomic(p,new,st);written.append(n)
  for n,(_,new,_) in prepared.items():require(read(root/n)==new,'Write mismatch '+n)
  verify_all(prepared,verify);prepare(root)
 except BaseException:
  failures=[]
  for n in reversed(written):
   old,new,st=changes[n];p=root/n
   try:
    require(read(p)==new,'Concurrent change blocks rollback '+n)
    if old is None:p.unlink()
    else:
     if n in removed_dirs:
      ds=removed_dirs[n];p.parent.mkdir(mode=ds.st_mode&0o7777);os.chown(p.parent,ds.st_uid,ds.st_gid)
     atomic(p,old,st);os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
    require(read(p)==old,'Rollback mismatch '+n)
   except Exception as e:failures.append(str(e))
  for d in reversed(created):
   try:d.rmdir()
   except OSError as e:failures.append(str(e))
  print('BACKUP='+str(backup));print('ROLLBACK='+('PASS' if not failures else str(failures)));raise
 print('CERTIFICATE_TEXT=PASS; HTTPS=PASS; HTML_UPDATED=1')
 print('PHOTOS_PRICES_POPUPS_AND_BOOKING=UNCHANGED; NGINX=UNCHANGED')
 print('BACKUP='+str(backup));print('Следующий шаг: пришлите вывод для внешней проверки сайта.')
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=p.parse_args()
 require(os.geteuid()==0 and socket.gethostname()=='az-server','Нужен root на az-server');require(ROOT.is_dir(),'Missing site root')
 with open('/run/lock/azmednsk-critical.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if a.check:prepare(ROOT);print('PREFLIGHT=PASS; LIVE_WRITES=NONE')
  else:apply(ROOT,BACKUPS)
if __name__=='__main__':
 try:main()
 except Exception as e:print('STOP:',e,file=sys.stderr);sys.exit(1)
