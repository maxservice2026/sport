# Specifikace aplikace SK MNÍŠECKO

## 1. Přehled a cíl
Aplikace pro sportovní klub, který organizuje tři sporty (atletika, fotbal, gymnastika) v různých věkových kategoriích.  
Administrátor pracuje na notebooku (web), trenéři používají mobilní telefony (web).  
Aplikace běží na vlastním serveru s databází.

## 2. Role a oprávnění
- **Administrátor**  
  Správa sportů, skupin, registrací, trenérů a nastavení docházky.
- **Trenér**  
  Přístup jen k docházce a zobrazení dětí ve svých skupinách.
- **Rodič (nepřihlášený uživatel)**  
  Vyplňuje veřejný registrační formulář.

## 3. Funkční požadavky

### 3.1 Správa sportů
- Systém obsahuje 3 sporty: **atletika**, **fotbal**, **gymnastika**.
- V každém sportu může admin vytvářet více tréninkových skupin.

### 3.2 Správa skupin
Pro každou skupinu:
- Název skupiny.
- Dny tréninků (zaškrtávací políčka Po–Ne).
- Nastavení docházky: až **5 různých možností docházky** s různou cenou.
  - Každá možnost obsahuje: název varianty (např. 1× týdně), frekvence, cena.

### 3.3 Registrace dětí (veřejný formulář)
Formulář pro registraci obsahuje:
- Volba sportu.
- Způsob identifikace dítěte:
  - **České rodné číslo včetně lomítka** (validace + ověření v databázi obyvatel).
  - **Cizinec**: zadat číslo pasu + jméno + příjmení.
- Kontaktní údaje dítěte:
  - Telefon dítěte (volitelné).
- Údaje o rodiči:
  - Jméno, příjmení, email, adresa (ulice, město, PSČ), telefon.

Po odeslání formuláře:
- Dítě se vytvoří jako záznam a je přiřazeno do vybrané skupiny.
- Záznam je viditelný administrátorovi ve skupině.

### 3.4 Správa registrovaných dětí
V detailu skupiny:
- Seznam registrovaných dětí.
- Akce nad dítětem:
  - Smazat (odstranit záznam, nebo jen zrušit členství ve skupině – viz otevřené otázky).
  - Klonovat do jiné skupiny (kopírovat záznam dítěte + nové členství).

### 3.5 Správa uživatelů (trenéři)
- Admin vytváří uživatele typu **trenér**.
- Trenér má omezený přístup: pouze docházka a seznam dětí ve svých skupinách.

### 3.6 Docházka (trenér)
První dostupná funkce pro trenéry:
- Trenér si vybere konkrétní skupinu.
- Zobrazí se seznam dětí v podobě **čtverečků**:
  - výchozí barva **červená**,
  - obsah: jméno + příjmení + % docházky.
- Kliknutím na čtvereček se záznam zbarví **zeleně** a tím se uloží docházka pro konkrétní tréninkový den.

Výpočet % docházky:
- % docházky = počet přítomností / maximální počet tréninků ve zvoleném období.
- Perioda a logika maxima jsou v otevřených otázkách.

## 4. Datový model (návrh)
Základní entity:
- **Sport** (id, název)
- **Skupina** (id, sport_id, název, dny_tréninku)
- **Docházková_varianta** (id, skupina_id, název, frekvence, cena)
- **Dítě** (id, rodné_číslo?, číslo_pasu?, jméno, příjmení, telefon?)
- **Rodič** (id, jméno, příjmení, email, telefon, ulice, město, psč)
- **Členství** (id, dítě_id, skupina_id, docházková_varianta_id, datum_registrace)
- **Trenér** (id, uživatel_id)
- **Trenérská_skupina** (trenér_id, skupina_id)
- **Tréninkový_den** (id, skupina_id, datum) – generované podle nastavených dnů
- **Docházka** (id, dítě_id, tréninkový_den_id, přítomen)

## 5. Nefunkční požadavky
- Webová aplikace optimalizovaná pro:
  - administraci (notebook / desktop),
  - trenéry (mobil).
- Vlastní server + databáze.
- Základní zabezpečení:
  - role-based access control,
  - audit změn docházky,
  - bezpečné ukládání osobních údajů (GDPR).

## 6. Integrace
- Ověření rodného čísla v databázi obyvatel (externí integrace).
  - Vyžaduje upřesnění dostupného rozhraní a přístupových údajů.

## 7. Uživatelské scénáře (user stories)

### 7.1 Administrátor
**US-ADM-1: Správa skupin**  
Jako administrátor chci vytvořit a upravit skupiny pro jednotlivé sporty, abych mohl organizovat tréninky.  
Kritéria: skupina má název, dny tréninků a až 5 docházkových variant s cenou.

**US-ADM-2: Registrace dětí**  
Jako administrátor chci vidět a spravovat registrace dětí ve skupinách, abych měl přehled o členech.  
Kritéria: ve skupině vidím seznam dětí, mohu dítě smazat nebo klonovat do jiné skupiny.

**US-ADM-3: Správa trenérů**  
Jako administrátor chci vytvářet trenérské účty a přiřazovat je ke skupinám, aby trenéři viděli jen své skupiny.  
Kritéria: trenér má omezená oprávnění a přiřazené skupiny.

### 7.2 Trenér
**US-TRN-1: Výběr skupiny**  
Jako trenér chci vybrat skupinu, abych viděl seznam dětí pro docházku.  
Kritéria: trenér vidí jen své skupiny.

**US-TRN-2: Zápis docházky**  
Jako trenér chci jednoduše zaznamenat docházku kliknutím na jméno dítěte, abych rychle uložil přítomnost.  
Kritéria: kliknutí změní barvu čtverečku na zelenou a uloží docházku pro konkrétní datum.

**US-TRN-3: Přehled docházky**  
Jako trenér chci vidět % docházky u každého dítěte, abych měl přehled o pravidelnosti.  
Kritéria: % docházky se počítá z reálných tréninků a zobrazuje se u každého dítěte.

### 7.3 Rodič (nepřihlášený)
**US-PAR-1: Registrace dítěte**  
Jako rodič chci zaregistrovat dítě přes webový formulář, abych ho přihlásil do sportovní skupiny.  
Kritéria: formulář umožní zadat rodné číslo (nebo pas), kontakty a vybraný sport/skupinu.

## 8. Návrh API (vysokoúrovňově)
Pozn.: Cesty a datové struktury jsou návrh, finální podoba závisí na zvoleném frameworku.

### 8.1 Autentizace a uživatel
- `POST /api/auth/login` – přihlášení (admin/trenér)
- `POST /api/auth/logout` – odhlášení
- `GET /api/me` – profil přihlášeného uživatele a role

### 8.2 Sporty a skupiny
- `GET /api/sports` – seznam sportů
- `GET /api/sports/:sportId/groups` – seznam skupin ve sportu
- `POST /api/groups` – vytvoření skupiny
- `PATCH /api/groups/:groupId` – úprava skupiny
- `DELETE /api/groups/:groupId` – smazání/archivace skupiny

### 8.3 Docházkové varianty
- `POST /api/groups/:groupId/attendance-options` – vytvoření varianty
- `PATCH /api/attendance-options/:optionId` – úprava varianty
- `DELETE /api/attendance-options/:optionId` – smazání varianty

### 8.4 Registrace a členství
- `POST /api/registrations` – veřejná registrace dítěte
- `GET /api/groups/:groupId/members` – seznam dětí ve skupině
- `PATCH /api/memberships/:membershipId` – změna varianty / přesun
- `POST /api/memberships/:membershipId/clone` – klonování do jiné skupiny
- `DELETE /api/memberships/:membershipId` – zrušení členství

### 8.5 Trenéři
- `POST /api/trainers` – vytvoření trenéra
- `GET /api/trainers` – seznam trenérů
- `PATCH /api/trainers/:trainerId` – úprava trenéra
- `POST /api/trainers/:trainerId/groups` – přiřazení skupin
- `DELETE /api/trainers/:trainerId/groups/:groupId` – odebrání skupiny

### 8.6 Docházka
- `GET /api/groups/:groupId/sessions?from=&to=` – tréninkové dny v období
- `POST /api/groups/:groupId/sessions` – vygenerovat tréninkové dny
- `GET /api/sessions/:sessionId/attendance` – docházka v daný den
- `POST /api/attendance` – uložit docházku (sessionId + childId)
- `GET /api/children/:childId/attendance-summary?from=&to=` – % docházky

## 9. Návrh obrazovek (UI)

### 9.1 Veřejná registrace (mobil/desktop)
- Registrace dítěte (formulář)
- Potvrzení registrace (děkovná stránka)

### 9.2 Administrace (desktop)
- Login
- Dashboard (přehled sportů a skupin)
- Detail sportu (seznam skupin)
- Detail skupiny
  - nastavení skupiny (název, dny, varianty docházky)
  - seznam dětí + akce smazat/klonovat
- Správa trenérů (seznam, vytvoření, přiřazení skupin)
- Přehled docházky (volitelně v detailu skupiny)

### 9.3 Trenér (mobil)
- Login
- Výběr skupiny (jen přiřazené)
- Docházka pro dnešní trénink
  - mřížka čtverečků (červená = nepřítomen, zelená = přítomen)
  - jméno + příjmení + % docházky na dlaždici
- Detail dítěte (volitelné, jen čtení)

## 10. Otevřené otázky
1. **Ověření rodného čísla**: Jaké rozhraní/zdroj bude použit pro “databázi obyvatel”?
2. **Registrace do skupin**: vybírá rodič i konkrétní skupinu, nebo jen sport a skupinu přidělí admin?
3. **Docházka – perioda výpočtu**: počítá se % za aktuální měsíc, sezónu, nebo od registrace?
4. **Maximální počet tréninků**: počítat podle nastavených dnů v týdnu, nebo podle reálně vytvořených tréninků?
5. **Mazání dítěte**: jde o smazání z databáze, nebo jen zrušení členství ve skupině?
6. **Více rodičů / kontaktů**: bude potřeba evidovat více zákonných zástupců?
7. **Věkové kategorie**: mají být součástí skupin nebo samostatné entity?
8. **Platby**: budou se evidovat platby a stav úhrady, nebo jen cena docházkové varianty?
9. **Jazyk**: pouze čeština, nebo vícejazyčnost?
