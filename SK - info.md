# SK - info

## Spouštěcí IP adresy (localhost:8789)
- `http://127.0.0.1:8789/` - domovská adresa (přesměruje podle role uživatele)
- `http://127.0.0.1:8789/login/` - přihlášení
- `http://127.0.0.1:8789/logout/` - odhlášení
- `http://127.0.0.1:8789/registrace/` - veřejná registrace dítěte
- `http://127.0.0.1:8789/admin/` - vlastní administrace (dashboard)
- `http://127.0.0.1:8789/admin/groups/` - skupiny
- `http://127.0.0.1:8789/admin/children/` - děti
- `http://127.0.0.1:8789/admin/trainers/` - trenéři
- `http://127.0.0.1:8789/admin/attendance/` - docházka
- `http://127.0.0.1:8789/admin/contributions/` - příspěvky
- `http://127.0.0.1:8789/django-admin/` - Django Admin
- `http://127.0.0.1:8789/trainer/` - trenérský přehled
- `http://127.0.0.1:8789/trainer/group/<group_id>/attendance/` - docházka trenéra pro konkrétní skupinu
- `http://127.0.0.1:8789/parent/` - rodičovský přehled
- `http://127.0.0.1:8789/parent/profile/` - profil rodiče
- `http://127.0.0.1:8789/parent/child/<child_id>/` - karta dítěte rodiče
- `http://127.0.0.1:8789/login/?mobile=1` - mobilní náhled v prohlížeči

## Přihlašovací údaje (známé)
- `admin@skmnisecko.test / admin123`
- Seed trenéři (`*@seed.skmnisecko.test`) - heslo: `trainer123`
- Seed rodiče (`rodicXXXX@seed.skmnisecko.test`) - heslo: `parent123`
- Ostatní ručně vytvořené účty - heslo není v DB čitelné (neznámé)

## Terminál 1 (server)
```bash
cd /Users/mpmp/Documents/SK\ Mnisecko\ app
source .venv/bin/activate
python manage.py runserver 127.0.0.1:8789
```

## Terminál 2 (pracovní příkazy)
```bash
cd /Users/mpmp/Documents/SK\ Mnisecko\ app
source .venv/bin/activate
```

### Typické příkazy v Terminálu 2
```bash
python manage.py migrate
python manage.py seed_club_data
python manage.py check
```

## Všechny uživatelské přihlášky (emaily dle role)

### Admin
- admin@skmnisecko.test
- jirka@sportujpodbrdy.cz

### Trenér
- hhhh@hhh.cz
- trena@trena.cz
- trener.atletika1@seed.skmnisecko.test
- trener.atletika2@seed.skmnisecko.test
- trener.atletika3@seed.skmnisecko.test
- trener.atletika4@seed.skmnisecko.test
- trener.fotbal1@seed.skmnisecko.test
- trener.fotbal2@seed.skmnisecko.test
- trener.fotbal3@seed.skmnisecko.test
- trener.gymnastika1@seed.skmnisecko.test
- trener.gymnastika2@seed.skmnisecko.test
- trener@trener.cz

### Rodič
- jirkarodeohrdlicka@gmail.com
- nevim@nevim.cz
- rodic0001@seed.skmnisecko.test
- rodic0002@seed.skmnisecko.test
- rodic0003@seed.skmnisecko.test
- rodic0004@seed.skmnisecko.test
- rodic0005@seed.skmnisecko.test
- rodic0006@seed.skmnisecko.test
- rodic0007@seed.skmnisecko.test
- rodic0008@seed.skmnisecko.test
- rodic0009@seed.skmnisecko.test
- rodic0010@seed.skmnisecko.test
- rodic0011@seed.skmnisecko.test
- rodic0012@seed.skmnisecko.test
- rodic0013@seed.skmnisecko.test
- rodic0014@seed.skmnisecko.test
- rodic0015@seed.skmnisecko.test
- rodic0016@seed.skmnisecko.test
- rodic0017@seed.skmnisecko.test
- rodic0018@seed.skmnisecko.test
- rodic0019@seed.skmnisecko.test
- rodic0020@seed.skmnisecko.test
- rodic0021@seed.skmnisecko.test
- rodic0022@seed.skmnisecko.test
- rodic0023@seed.skmnisecko.test
- rodic0024@seed.skmnisecko.test
- rodic0025@seed.skmnisecko.test
- rodic0026@seed.skmnisecko.test
- rodic0027@seed.skmnisecko.test
- rodic0028@seed.skmnisecko.test
- rodic0029@seed.skmnisecko.test
- rodic0030@seed.skmnisecko.test
- rodic0031@seed.skmnisecko.test
- rodic0032@seed.skmnisecko.test
- rodic0033@seed.skmnisecko.test
- rodic0034@seed.skmnisecko.test
- rodic0035@seed.skmnisecko.test
- rodic0036@seed.skmnisecko.test
- rodic0037@seed.skmnisecko.test
- rodic0038@seed.skmnisecko.test
- rodic0039@seed.skmnisecko.test
- rodic0040@seed.skmnisecko.test
- rodic0041@seed.skmnisecko.test
- rodic0042@seed.skmnisecko.test
- rodic0043@seed.skmnisecko.test
- rodic0044@seed.skmnisecko.test
- rodic0045@seed.skmnisecko.test
- rodic0046@seed.skmnisecko.test
- rodic0047@seed.skmnisecko.test
- rodic0048@seed.skmnisecko.test
- rodic0049@seed.skmnisecko.test
- rodic0050@seed.skmnisecko.test
- rodic0051@seed.skmnisecko.test
- rodic0052@seed.skmnisecko.test
- rodic0053@seed.skmnisecko.test
- rodic0054@seed.skmnisecko.test
- rodic0055@seed.skmnisecko.test
- rodic0056@seed.skmnisecko.test
- rodic0057@seed.skmnisecko.test
- rodic0058@seed.skmnisecko.test
- rodic0059@seed.skmnisecko.test
- rodic0060@seed.skmnisecko.test
- rodic0061@seed.skmnisecko.test
- rodic0062@seed.skmnisecko.test
- rodic0063@seed.skmnisecko.test
- rodic0064@seed.skmnisecko.test
- rodic0065@seed.skmnisecko.test
- rodic0066@seed.skmnisecko.test
- rodic0067@seed.skmnisecko.test
- rodic0068@seed.skmnisecko.test
- rodic0069@seed.skmnisecko.test
- rodic0070@seed.skmnisecko.test
- rodic0071@seed.skmnisecko.test
- rodic0072@seed.skmnisecko.test
- rodic0073@seed.skmnisecko.test
- rodic0074@seed.skmnisecko.test
- rodic0075@seed.skmnisecko.test
- rodic0076@seed.skmnisecko.test
- rodic0077@seed.skmnisecko.test
- rodic0078@seed.skmnisecko.test
- rodic0079@seed.skmnisecko.test
- rodic0080@seed.skmnisecko.test
- rodic0081@seed.skmnisecko.test
- rodic0082@seed.skmnisecko.test
- rodic0083@seed.skmnisecko.test
- rodic0084@seed.skmnisecko.test
- rodic0085@seed.skmnisecko.test
- rodic0086@seed.skmnisecko.test
- rodic0087@seed.skmnisecko.test
- rodic0088@seed.skmnisecko.test
- rodic0089@seed.skmnisecko.test
- rodic0090@seed.skmnisecko.test
- rodic0091@seed.skmnisecko.test
- rodic0092@seed.skmnisecko.test
- rodic0093@seed.skmnisecko.test
- rodic0094@seed.skmnisecko.test
- rodic0095@seed.skmnisecko.test
- rodic0096@seed.skmnisecko.test

## Skupiny (group_id pro URL docházky)
- [3] Atletika - A1
- [9] Atletika - A2
- [10] Atletika - B1
- [4] Atletika - B2
- [5] Atletika - C1
- [11] Atletika - C2
- [12] Atletika - D1
- [15] Atletika - TEST-SMAZAT
- [7] Fotbal - U11
- [8] Fotbal - U13
- [2] Fotbal - U7
- [6] Fotbal - U9
- [13] Gymnastika - G-mix
- [14] Gymnastika - G-závodní
