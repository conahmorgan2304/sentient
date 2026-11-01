# Sentient

<p align="center"><img src="https://raw.githubusercontent.com/XAV59213/sentient/main/docs/images/sentient.jpg" alt="Sentient" width="420"></p>

**Un cerveau IA évolutif pour Home Assistant.**

Sentient donne à votre maison un cerveau partagé. Chaque assistant vocal et chaque automatisation utilise la même mémoire, suit les mêmes règles de la maison (quand et où parler) et devient plus intelligent avec le temps.

Il fonctionne avec l’IA que vous avez déjà dans Home Assistant (Google Gemini, OpenAI, Ollama, ou tout agent de conversation). Il n’a pas besoin de sa propre clé API.

> **Statut : bêta.** Utilisé quotidiennement dans une maison ; retours et issues bienvenus. Nécessite Home Assistant 2025.10 ou plus récent, un agent de conversation et, pour les fonctions caméra et arrière-plan, une entité AI Task.

## Ce qu’il fait aujourd’hui

- **Mémoire partagée à long terme.** « Souviens-toi que le clapier des lapins est près de l’abri. » N’importe quelle voix de la maison peut ensuite le rappeler. Les quasi-doublons sont fusionnés.
- **Un seul cerveau pour toutes les voix.** Cochez **Sentient** dans *Contrôler Home Assistant* de n’importe quel agent. Il reçoit les souvenirs du foyer et des outils pour se souvenir, rappeler, oublier et annoncer.
- **Règles de parole de la maison.** `sentient.say` et les outils vocaux :
  - respectent les heures de silence (les messages urgents passent quand même) ;
  - suppriment les mots que vous ne voulez jamais entendre (« monsieur », …) ;
  - éliminent les doublons ;
  - restent silencieux un certain temps après certaines automatisations ;
  - parlent là où se trouvent réellement les personnes.
- **Annonces sur les Echo.** Via Alexa Media Player (type `tts` forcé pour une meilleure compatibilité).
- **Timeline.** Journal de ce que la maison a fait et remarqué.
- **Notifications téléphone.** Choisissez les téléphones et ce qui leur est envoyé.
- **Bienvenue à la maison.** Optionnel : un message quand quelqu’un rentre après une absence.

## Installation

1. Ajoutez ce dépôt dans HACS (Custom repositories).
2. Installez **Sentient**.
3. Redémarrez Home Assistant.
4. Ajoutez l’intégration et suivez l’assistant.

## Configuration minimale pour les Echo

- Ajoutez vos Echo (via Alexa Media Player) dans **Haut-parleurs**.
- Désactivez **Echo : alarmes et rappels uniquement** si vous voulez que tous les messages passent.
- Utilisez `sentient.say` pour les annonces.

---

*Version actuelle : 0.65.6*
