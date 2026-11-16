# GOAD-Light

![GOAD Light overview](../../docs/img/GOAD-Light_schema.png)

This is a light version of goad without the essos domain. This lab was build for computer with less performance.
Missing scenarios:
- cross forest exploitation (no more external forest)
- mssql trusted link
- Historical exploitability outside the selected Windows build and explicit lab settings; provisioning alone does not prove an exploit works.

Local source adds Legacy LAPS, gMSA and selected ESC2/3/4/6/7/9/10/11/13/15 conditions while retaining the single DC01 CA. The libvirt DC02 uses the selected historical2019 image for Zerologon/EFSRPC/noPac prerequisites. These isolated lab conditions preserve intentional weaknesses; they are not production guidance or tested attack results. See the local libvirt provider guide for exact build and scope.

### Servers
The base lab has three virtual machines; optional Exchange adds SRV01 (`the-eyrie`) as a fourth:
- **kingslanding** : DC01  running on Windows Server 2019 (with windefender enabled by default)
- **winterfell**   : DC02  running on Windows Server 2019 (with windefender enabled by default)
- **castelblack**  : SRV02 running on Windows Server 2019 (with windefender **disabled** by default)

#### domain : north.sevenkingdoms.local
- **winterfell**     : DC02
- **castelblack**    : SRV02 : MSSQL / IIS

#### domain : sevenkingdoms.local
- **kingslanding**   : DC01


The lab setup is automated using vagrant and ansible automation tools.
You can change the vm version in the Vagrantfile according to Stefan Scherer vagrant repository : https://app.vagrantup.com/StefanScherer


### Users/Groups and associated vulnerabilites/scenarios

- You can find a lot of the available scenarios on [https://mayfly277.github.io/categories/ad/](https://mayfly277.github.io/categories/ad/)

NORTH.SEVENKINGDOMS.LOCAL
- STARKS:              RDP on WINTERFELL AND CASTELBLACK
  - arya.stark:        Execute as user on mssql
  - eddard.stark:      DOMAIN ADMIN NORTH/ (bot 5min) LLMRN request to do NTLM relay with responder
  - catelyn.stark:     
  - robb.stark:        bot (3min) RESPONDER LLMR
  - sansa.stark:       
  - brandon.stark:     ASREP_ROASTING
  - rickon.stark:      
  - theon.greyjoy:
  - jon.snow:          mssql admin / KERBEROASTING / group cross domain / mssql trusted link
  - hodor:             PASSWORD SPRAY (user=password)
- NIGHT WATCH:         RDP on CASTELBLACK
  - samwell.tarly:     Password in ldap description / mssql execute as login
                       GPO abuse (Edit Settings on "STARKWALLPAPER" GPO)
  - jon.snow:          (see starks)
  - jeor.mormont:      (see mormont)
- MORMONT:             RDP on CASTELBLACK
  - jeor.mormont:      ACL writedacl-writeowner on group Night Watch
- AcrossTheSea :       cross forest group

SEVENKINGDOMS.LOCAL
- LANISTERS
  - tywin.lannister:   ACL forcechangepassword on jaime.lanister
  - jaime.lannister:   ACL genericwrite-on-user joffrey.baratheon
  - tyron.lannister:   ACL self-self-membership-on-group Small Council
  - cersei.lannister:  DOMAIN ADMIN SEVENKINGDOMS
- BARATHEON:           RDP on KINGSLANDING
  - robert.baratheon:  DOMAIN ADMIN SEVENKINGDOMS
  - joffrey.baratheon: ACL Write DACL on tyron.lannister
  - renly.baratheon:
  - stannis.baratheon: ACL genericall-on-computer kingslanding / ACL writeproperty-self-membership Domain Admins
- SMALL COUNCIL :      ACL add Member to group dragon stone / RDP on KINGSLANDING
  - petyer.baelish:    ACL writeproperty-on-group Domain Admins
  - lord.varys:        ACL genericall-on-group Domain Admins / Acrossthenarrossea
  - maester.pycelle:   ACL write owner on group Domain Admins
- DRAGONSTONE :        ACL Write Owner on KINGSGUARD
- KINGSGUARD :         ACL generic all on user stannis.baratheon
- AccorsTheNarrowSea:       cross forest group


### Computers Users and group permissions

- SEVENKINGDOMS
  - DC01 : kingslanding.sevenkingdoms.local (Windows Server 2019) (SEVENKINGDOMS DC)
    - Admins : robert.baratheon (U), cersei.lannister (U)
    - RDP: Small Council (G)

- NORTH
  - DC02 : winterfell.north.sevenkingdoms.local (Windows Server 2019) (NORTH DC)
    - Admins : eddard.stark (U), catelyn.stark (U), robb.stark (U)
    - RDP: Stark(G)

  - SRV02 : castelblack.north.sevenkingdoms.local (Windows Server 2019) (IIS, MSSQL, SMB share)
    - Admins: jeor.mormont (U)
    - RDP: Night Watch (G), Mormont (G), Stark (G)
    - IIS : allow asp upload, run as NT Authority/network
    - MSSQL:
      - admin : jon.snow
      - impersonate : 
        - execute as login : samwel.tarlly -> sa
        - execute as user : arya.stark -> dbo
