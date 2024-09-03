from kubernetes import client, config
from kubernetes.client import V1Deployment, V1DeploymentSpec, V1ObjectMeta, V1PodSpec, V1Container, V1ResourceRequirements
from kubernetes.client import V1Service, V1ServiceSpec, V1ServicePort, V1Ingress, V1IngressSpec, V1IngressRule, V1HTTPIngressPath, V1HTTPIngressRuleValue
from kubernetes.client import V1PersistentVolumeClaim, V1PersistentVolumeClaimSpec, V1ResourceRequirements, V1IngressTLS, V1ServiceBackendPort
from kubernetes.client import V1IngressBackend, V1IngressServiceBackend, V1ConfigMap

def create_namespace(api_instance, namespace):
    ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
    api_instance.create_namespace(ns)

def create_deployment(api_instance, namespace, name, image, memory, cpu, config_map_name=None):
    volumes = []
    volume_mounts = []
    if config_map_name:
        volumes.append({'name': 'config-volume', 'configMap': {'name': config_map_name}})
        volume_mounts.append({'name': 'config-volume', 'mountPath': '/etc/prometheus/prometheus.yml', 'subPath': 'prometheus.yml'})

    deployment = V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=V1ObjectMeta(name=name, namespace=namespace),
        spec=V1DeploymentSpec(
            replicas=1,
            selector={'matchLabels': {'app': name}},
            template={
                'metadata': {'labels': {'app': name}},
                'spec': V1PodSpec(
                    containers=[V1Container(
                        name=name,
                        image=image,
                        ports=[{'containerPort': 9090 if name == 'prometheus' else 3000}],
                        resources=V1ResourceRequirements(
                            requests={'memory': memory, 'cpu': cpu},
                            limits={'memory': memory, 'cpu': cpu}
                        ),
                        volume_mounts=volume_mounts
                    )],
                    volumes=volumes
                )
            }
        )
    )
    api_instance.create_namespaced_deployment(namespace=namespace, body=deployment)

def create_service(api_instance, namespace, name):
    service = V1Service(
        api_version="v1",
        kind="Service",
        metadata=V1ObjectMeta(name=f"{name}-svc", namespace=namespace),
        spec=V1ServiceSpec(
            selector={'app': name},
            ports=[V1ServicePort(port=3000 if name == 'grafana' else 9090, target_port=3000 if name == 'grafana' else 9090)],
            type='ClusterIP'
        )
    )
    api_instance.create_namespaced_service(namespace=namespace, body=service)

def create_pvc(api_instance, namespace, name):
    pvc = V1PersistentVolumeClaim(
        api_version="v1",
        kind="PersistentVolumeClaim",
        metadata=V1ObjectMeta(name=f"{name}-pvc", namespace=namespace),
        spec=V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=V1ResourceRequirements(requests={"storage": "1Gi"})
        )
    )
    api_instance.create_namespaced_persistent_volume_claim(namespace=namespace, body=pvc)

def create_ingress(api_instance, namespace, name, subdomain):
    ingress = V1Ingress(
        api_version="networking.k8s.io/v1",
        kind="Ingress",
        metadata=V1ObjectMeta(name=f"{name}-ingress", namespace=namespace, annotations={
            "cert-manager.io/cluster-issuer": "letsencrypt-prod"
        }),
        spec=V1IngressSpec(
            ingress_class_name="nginx",
            rules=[
                V1IngressRule(
                    host=f"{subdomain}.{namespace}.ramanuj.dev",
                    http=V1HTTPIngressRuleValue(
                        paths=[
                            V1HTTPIngressPath(
                                path="/",
                                path_type="Prefix",
                                backend=V1IngressBackend(
                                    service=V1IngressServiceBackend(
                                        name=f"{name}-svc",
                                        port=V1ServiceBackendPort(number=3000 if name == 'grafana' else 9090)
                                    )
                                )
                            )
                        ]
                    )
                )
            ],
            tls=[
                V1IngressTLS(
                    hosts=[f"{subdomain}.{namespace}.ramanuj.dev"],
                    secret_name=f"{name}-tls"
                )
            ]
        )
    )
    api_instance.create_namespaced_ingress(namespace=namespace, body=ingress)

def create_prometheus_config_map(core_api, namespace):
    config_data = """
scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
  - job_name: 'postgres-exporter'
    static_configs:
      - targets: ['exporter-svc.postgres.svc.cluster.local:9187']
"""
    config_map = V1ConfigMap(
        api_version="v1",
        kind="ConfigMap",
        metadata=V1ObjectMeta(name='prometheus-config', namespace=namespace),
        data={"prometheus.yml": config_data}
    )
    core_api.create_namespaced_config_map(namespace=namespace, body=config_map)

def main(namespace, memory, cpu):
    config.load_kube_config()
    api_instance = client.AppsV1Api()
    core_api = client.CoreV1Api()
    networking_api = client.NetworkingV1Api()

    # Create Namespace
    create_namespace(core_api, namespace)

    # Create Prometheus ConfigMap
    create_prometheus_config_map(core_api, namespace)

    # Create Deployments
    create_deployment(api_instance, namespace, 'prometheus', 'prom/prometheus:latest', memory, cpu, config_map_name='prometheus-config')
    create_deployment(api_instance, namespace, 'grafana', 'grafana/grafana:latest', memory, cpu)

    # Create Services
    create_service(core_api, namespace, 'prometheus')
    create_service(core_api, namespace, 'grafana')

    # Create PVCs
    create_pvc(core_api, namespace, 'prometheus')
    create_pvc(core_api, namespace, 'grafana')

    # Create Ingress with different subdomains
    create_ingress(networking_api, namespace, 'prometheus', 'prometheus')
    create_ingress(networking_api, namespace, 'grafana', 'grafana')

if __name__ == "__main__":
    namespace = input("Enter the namespace: ")
    memory = input("Enter memory allocation (e.g., 512Mi): ")
    cpu = input("Enter CPU allocation (e.g., 500m): ")
    main(namespace, memory, cpu)
