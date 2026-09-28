# Læs det her før du gør noget

Skrevet 28. september 2026 fra Vesterbro-Mac'en. Simon beder dig læse filen inden du deployer, genbygger siden eller rører en Pi.

Træk det her commit først. Kør ikke den `scripts/hubctl`, der allerede ligger på din maskine. Den gamle udgave kan stadig lægge en lokal `backend/static` oven på havens side.

## Hvad der blev slettet

Søndag 27. september 2026 kl. 19:08 kørte den her Mac `hubctl backend garden`. Kommandoen kopierede hele `backend/`, også `backend/static`. På den her Mac var den mappe et frontend-build fra lørdag 26. september kl. 14:26.

Det build erstattede havens `index.html`. Siden peger derefter på

`/_app/immutable/nodes/2.BLgytyVw.js`

Den fil har knappen "sidst hjemme" og nul forekomster af `evidence-count`. Bladren er væk på havekiosken.

Selve den `index.html`, haven viste før kl. 19:08, er væk. Den lå kun på Pi'en. Den ligger ikke i git. Den ligger ikke på den her Mac. `scp` sletter ikke de hashed filer, den ikke overskriver, så nyere chunks kunne stadig hentes bagefter:

- `2.BWLLKU3J.js` (26. sep 20:30) har `evidence-count`
- `2.DVsc4GR7.js` (26. sep 20:56) har `evidence-count`

Jeg ved ikke, hvilken af dem havens index pegede på. Gæt ikke. Tjek mtimes på Pi'en, før du regner med at filerne stadig ligger der.

Det her blev ikke slettet:

- Evidence-billederne. Mandag morgen svarede begge hubs `GET /api/security/evidence` med 389 items. Et id derfra er `person-20260927T134152Z`.
- Hjemmets side. Vesterbro-kiosken loader `2.BWLLKU3J.js` og kan bladre. Dens `staticId` ved health 28. sep kl. 18:40 var `39a5d8dcab8c8bd88ac4800a6fd65cc81a428c9d`. Hjemmets backend-proces er stadig release `1329bbfd8ea2f1b4c7791d2e06864389c7737b5d`.
- Havnens Python-proces blev genstartet med mains-off (nedenfor). Release på haven er stadig `b0e12fccffa459822237f5abe7c9788e0c2700eb`.

`static-id` på haven blev sat til `e13258ba3693a3e885efe636dd7565f7e2458264`. Det er det commit, backend-kopien kom fra. Det er ikke den side, filerne er. Ingen af hubberne har en `buildId` endnu. Den gamle lås læser kun stemplet, så den tror haven er på det commit og tillader et nyere static-deploy. Lad være.

## Sådan kommer siden tilbage

Simon forklarer i morgen, hvilken side haven skal vise. Byg ikke `main` og kør `hubctl static` for at "rette" den. Han har afvist det. Et build af nuværende `main` er en ny side, ikke den haven havde før kl. 19:08.

Kilden til bladren er allerede på `main`: `c9a9488` ("Let the sidst-hjemme modal page through every evidence still"), i `frontend/src/lib/CameraCard.svelte` og `frontend/src/lib/cameraEvidence.ts`.

Hvis din maskine stadig har den `backend/static`, du sidst lagde på haven, er det den eneste komplette kopi af den side. Kopier den ikke over med scp, og lad ikke den gamle `hubctl` gøre det. Sig til Simon at mappen findes, og vent.

Den nye `hubctl static` uploader aldrig checkoutets `backend/static`. Den bygger det commit, der er `origin/main`, ind i en frisk mappe. Den kan ikke lægge et gemt gammelt build tilbage.

## Det der skal med, når en side igen bliver lagt ud

- Bladren fra `c9a9488`. Det er det, Simon mistede på havekiosken.
- Spring fullscreen-prompten over, når siden allerede kører som installeret app (`matchMedia('(display-mode: fullscreen), (display-mode: standalone)')` før `requestFullscreen()`). Den opførsel sad i havens side før overskrivningen og sidder i hjemmets.
- Mains-off bliver på havens backend. Når Fossibot siger at 230 V er væk, holdes alle lamper slukket i cachen, og der sendes ikke til pærer, der ikke kan svare. Det er `0247900`, merget i `e13258b`, og det kører på haven. Tag det ikke ud.
- Loft-pæren (`2c573ed`) og podcast-kataloget i Firestore (`39a5d8d`) er jeres commits. De skal blive.
- Hjemmets tablet bliver på `https://ejdersted-home-hub.tail7947c4.ts.net:8443` i Haven-WebAPK'en. Åbn ikke `https://192.168.86.16:8443` på den tablet.
- Rør ikke hjemmets side, mens havens bliver afklaret. Hjemmet har stadig bladren.

## Sådan spiller deploy fremover

Siden bor i `/home/simmoe/HUE_EJDERSTED/served`, søskende til `backend/`. En kopi af `backend/` kan ikke erstatte den, når processen er den nye og `served/index.html` findes.

`hubctl backend` uploader kun Python. `static/` og `served/` kan ikke komme med. Den skriver ikke `static-id`. Den genstarter hue. Første gang den nye kode starter, og `served/` endnu ikke findes, serverer den stadig `backend/static`.

`hubctl static` nægter en dirty tree, en HEAD der ikke er `origin/main`, og en side hvis indbagte commit ikke er stamfar til HEAD. Den bygger til en midlertidig mappe (`HUE_STATIC_OUT`). Den læser ikke `backend/static`. Sha'en skrives ind i `index.html` som `<meta name="hue-build">` og i filen `build-id`. Pi'en pakker ud i `served.new` og flytter den først over `served/`, når `build-id` er identisk med HEAD. `static-id` skrives i samme skridt. Stemmer det ikke, bliver den side der allerede ligger, liggende.

Næste deploy læser `served/build-id` før stemplet. Mangler den, bruges `backend/static/build-id`, og ellers stemplet. `/api/health` viser `staticId`, `buildId` og `staticMismatch`. Når de to sha'er er sat og forskellige, er stemplet ikke siden. Tro `buildId`.

Første static-deploy, der opretter `served/`, genstarter hue, fordi processen vælger mappen ved opstart. Senere static-deploys bytter filer i den mappe, den allerede læser, og genstarter ikke. En hue-genstart stopper afspilning i haven.

Rækkefølgen, når Simon har sagt hvilken side der skal ud: pull, `hubctl backend` på den hub der skal have koden, derefter `hubctl static` på den hub han har navngivet. Indtil begge er kørt, viser kiosken stadig den gamle `backend/static`.

Scp ikke `backend/`, `backend/static` eller `served` selv. Den her Macs `backend/static` er stadig lørdagens build. Lad den ligge.
