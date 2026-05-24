# components/kafka-cluster/base — Placeholder

À remplir phase 2 — Cluster Kafka IUN via AMQ Streams.

AMQ Streams Operator est déjà installé sur le cluster (autre équipe). On déploie ici uniquement la CR `Kafka` IUN + `KafkaTopic` + `KafkaUser`, pas la Subscription.

## À définir avant remplissage

- Mode : KRaft (no ZK) ou Zookeeper — préférer KRaft (AMQ Streams >= 2.7).
- Brokers : 3 minimum pour quorum, anti-affinity par node.
- Storage : `ocs-storagecluster-ceph-rbd` via ODF.
- Listeners : interne (plaintext) + externe TLS (Route OCP).
- Topics initiaux : à coordonner avec les équipes applicatives.
