# Déploiement FuelLayer sur Dokploy

Le projet utilise l’environnement `production` existant dans **FuelLayer** sur
https://dokploy.almacare.me. Aucun environnement staging supplémentaire n’est requis.
Le domaine retenu est `api.fuellayer.16nlabs.com`.

## Services

- PostgreSQL 17, base et utilisateur `fuellayer`, stockage persistant géré par Dokploy.
  Laisser **External Port** vide. Conserver la base sur `dokploy-network`.
- Un service **Docker Compose**, connexion **Github-16nLabs**, dépôt
  `DevTeam-16nLabs/fuellayer-backend`, branche `main`, chemin `./compose.deploy.yaml`.
  Choisir Docker Compose, pas Docker Stack : la stack construit l’image depuis le dépôt.
- La stack lance `migrate` (Alembic puis catalogue), `api` et `worker` avec la même image.
  Le worker attend une API disponible ; l’API attend la réussite des migrations.

Dans **Environment**, adapter [.env.deploy.example](../.env.deploy.example).
`DATABASE_URL` est l’URL **interne** de la base Dokploy. Les mots de passe contenant des
caractères réservés doivent être encodés dans l’URL. Les URL `postgresql://` sont acceptées.
Clerk doit appartenir à FuelLayer et correspondre à la clé publique utilisée par le mobile.
Configurer le webhook `user.deleted` vers
`https://api.fuellayer.16nlabs.com/api/v1/integrations/clerk/webhook`, puis enregistrer
son secret dans `CLERK_WEBHOOK_SIGNING_SECRET`.

`OPENAI_API_KEY` ou `OPENROUTER_API_KEY` active les fonctions nécessitant l’IA.
`GOOGLE_PLACES_API_KEY` active la recherche de magasins. Le catalogue Ciqual ne dépend
pas de ces fournisseurs. Ne pas réutiliser les secrets StrengthLayer par défaut.

Dans **Domains**, ajouter `api.fuellayer.16nlabs.com`, service `api`, port `8000`,
chemin `/`, HTTPS avec Let's Encrypt. Le DNS doit atteindre le serveur Dokploy.
L’API doit être accessible par l’app sans écran Cloudflare Access ; la protection du
panneau Dokploy peut rester indépendante. Ne pas publier de port PostgreSQL.

## Déployer une révision validée

Attendre les trois contrôles GitHub : `Backend quality`, `Backend tests and migrations`
et `Backend deployment image`. Le dernier construit et teste la vraie stack avec une
base jetable, y compris l’absence de base, les migrations et le double import du catalogue.

Renseigner le SHA complet du commit validé dans `RELEASE_SHA`. Déployer lorsque `main`
correspond encore à ce commit et vérifier la révision dans le journal de déploiement.
Si la branche avance entre-temps, arrêter cette release et valider le nouveau commit.
Pour figer une prochaine release, sélectionner une branche de déploiement pointant
sur le commit validé. Le SHA est une métadonnée de build : il doit correspondre au code
sélectionné, pas à une valeur arbitraire. Garder Autodeploy désactivé tant que ce choix
et les migrations sont manuels.

Enregistrer les variables, puis **Deploy**. Vérifier que `migrate` termine avec le code 0,
que `api` est healthy et que `worker` reste démarré. Vérifier depuis l’extérieur :

```bash
curl --fail https://api.fuellayer.16nlabs.com/api/v1/ready
curl --fail 'https://api.fuellayer.16nlabs.com/api/v1/foods/search?q=riz&locale=fr&limit=1'
curl -i https://api.fuellayer.16nlabs.com/api/v1/me/bootstrap
```

La première réponse doit contenir `status: ready` et le SHA déployé ; la deuxième un
aliment ; la dernière doit renvoyer 401 sans session. Cela ne remplace pas une connexion
Clerk réelle et un test d’import de recette. `/health` ne vérifie que le processus ;
`/ready` vérifie PostgreSQL, la tête Alembic et un catalogue chargé.

Configurer ensuite une sauvegarde PostgreSQL avec une destination Dokploy et vérifier
une restauration sur une base séparée. Avant les déploiements suivants, sauvegarder la
base et vérifier la compatibilité des migrations avec les apps déjà installées.
Ne pas automatiser de downgrade de données. Cette stack ne garantit pas un déploiement
sans interruption pendant les migrations.

## Vérifier localement l’image

Avec Docker actif : `python3 scripts/check_container.py`.
Le script crée sa propre base et son réseau, puis les supprime même en cas d’échec.
Il ne charge pas les secrets locaux et ne touche pas à la base Dokploy.

Référence : [Docker Compose dans Dokploy](https://docs.dokploy.com/docs/core/docker-compose).
