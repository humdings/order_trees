# Order Trees

I wrote this for fun and it is actually pretty interesting.

This keeps a directory of json files that represent order data for trades.
Any time the order changes elsewhere, it gets written to json too.

The fun part is the orders can be chained into a binary tree and used
to keep track of an order book. 

Orders can be staged and checked if triggered. 


I wanted to duplicate a dict in a program in a json file on
disc, where changes made to either one are reflected in the other.

This is what it turned into one evening.

Meant for slow traders...
