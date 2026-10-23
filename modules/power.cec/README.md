# power.cec

Pilote d'alimentation **côté player** (cible `player`, pas d'entrypoint Python). Le code vit dans le bundle du
player (`apps/player/src/power/cec.ts`) car il doit fonctionner hors ligne ; ce dossier porte le manifeste
et l'i18n. Il appelle le pont natif `VdsNative.cec(action)` (Android, `packaging/android`) ; sans pont
(Tizen, Pi, navigateur) `available()` est faux et la cascade passe à `power.blank`.
Contrainte : le CEC Android exige la permission système `HDMI_CEC` (app privilégiée) ; sinon `power.cec.denied`.
