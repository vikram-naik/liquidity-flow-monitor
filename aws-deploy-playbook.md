1) push image lfm-app:2.0
2) upload the database.
3) check how to schedule the daily sync script using the container.
4) clean out the trading_* database tables.
5) update the nginx config to route requests to lfm app for /de/ uri..
6) just host it as another container instead of using dokcer compose. You would need to get the start script sorted.