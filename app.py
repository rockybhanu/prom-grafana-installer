from flask import Flask, request, jsonify, send_from_directory
from kubernetes import client, config
import time

app = Flask(__name__)

def create_kubernetes_stack(namespace, memory, cpu):
    config.load_kube_config()
    api_instance = client.AppsV1Api()
    core_api = client.CoreV1Api()
    networking_api = client.NetworkingV1Api()

    # Create Namespace
    ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
    core_api.create_namespace(ns)

    # Create Prometheus ConfigMap
    config_map = client.V1ConfigMap(
        api_version="v1",
        kind="ConfigMap",
        metadata=client.V1ObjectMeta(name='prometheus-scrape-configs', namespace=namespace),
        data={"prometheus.yml": "scrape_configs:\n  - job_name: 'postgres-exporter'\n    static_configs:\n      - targets: ['exporter-svc.postgres.svc.cluster.local:9187']\n  - job_name: 'ecommerce'\n    metrics_path: '/actuator/prometheus'\n    static_configs:\n      - targets: ['app-svc.app.svc.cluster.local:8080']\n"}
    )
    core_api.create_namespaced_config_map(namespace=namespace, body=config_map)

    # Create PVCs with 10 GB size
    create_pvc(core_api, namespace, 'prometheus')
    create_pvc(core_api, namespace, 'grafana')

    # Create Prometheus Deployment
    create_deployment(api_instance, namespace, 'prometheus', 'prom/prometheus:v2.52.0', memory, cpu, config_map_name='prometheus-scrape-configs', pvc_name='prometheus-pvc')

    # Create Grafana Deployment
    create_deployment(api_instance, namespace, 'grafana', 'grafana/grafana-oss:11.0.0-ubuntu', memory, cpu, pvc_name='grafana-pvc')

    # Create Services
    create_service(core_api, namespace, 'prometheus')
    create_service(core_api, namespace, 'grafana')

    # Create Ingress with different subdomains
    create_ingress(networking_api, namespace, 'prometheus', 'prometheus')
    create_ingress(networking_api, namespace, 'grafana', 'grafana')

def create_pvc(api_instance, namespace, name):
    pvc = client.V1PersistentVolumeClaim(
        api_version="v1",
        kind="PersistentVolumeClaim",
        metadata=client.V1ObjectMeta(name=f"{name}-pvc", namespace=namespace),
        spec=client.V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=client.V1ResourceRequirements(requests={"storage": "10Gi"})
        )
    )
    api_instance.create_namespaced_persistent_volume_claim(namespace=namespace, body=pvc)

def create_deployment(api_instance, namespace, name, image, memory, cpu, config_map_name=None, pvc_name=None):
    volumes = []
    volume_mounts = []

    if config_map_name:
        volumes.append(client.V1Volume(
            name='prom-config-volume',
            config_map={'name': config_map_name}
        ))
        volume_mounts.append(client.V1VolumeMount(
            name='prom-config-volume',
            mount_path='/etc/prometheus/'
        ))

    if pvc_name:
        volumes.append(client.V1Volume(
            name='prom-volume' if name == 'prometheus' else 'grafana-volume',
            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=pvc_name)
        ))
        volume_mounts.append(client.V1VolumeMount(
            name='prom-volume' if name == 'prometheus' else 'grafana-volume',
            mount_path='/prometheus' if name == 'prometheus' else '/var/lib/grafana'
        ))

    pod_security_context = client.V1PodSecurityContext(fs_group=1000)
    container_security_context = client.V1SecurityContext(run_as_user=1000, run_as_group=1000)

    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(name=name, namespace=namespace),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector={'matchLabels': {'app': name}},
            template={
                'metadata': {'labels': {'app': name}},
                'spec': client.V1PodSpec(
                    security_context=pod_security_context,
                    containers=[client.V1Container(
                        name=name,
                        image=image,
                        ports=[{'containerPort': 9090 if name == 'prometheus' else 3000}],
                        args=[
                            '--config.file=/etc/prometheus/prometheus.yml',
                            '--storage.tsdb.path=/prometheus'
                        ] if name == 'prometheus' else [],
                        resources=client.V1ResourceRequirements(
                            requests={'memory': memory, 'cpu': cpu},
                            limits={'memory': memory, 'cpu': cpu}
                        ),
                        volume_mounts=volume_mounts,
                        security_context=container_security_context
                    )],
                    volumes=volumes
                )
            }
        )
    )
    api_instance.create_namespaced_deployment(namespace=namespace, body=deployment)

def create_service(core_api, namespace, name):
    service = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(name=f"{name}-svc", namespace=namespace),
        spec=client.V1ServiceSpec(
            selector={'app': name},
            ports=[client.V1ServicePort(port=3000 if name == 'grafana' else 9090, target_port=3000 if name == 'grafana' else 9090)],
            type='ClusterIP'
        )
    )
    core_api.create_namespaced_service(namespace=namespace, body=service)

def create_ingress(networking_api, namespace, name, subdomain):
    ingress = client.V1Ingress(
        api_version="networking.k8s.io/v1",
        kind="Ingress",
        metadata=client.V1ObjectMeta(name=f"{name}-ingress", namespace=namespace, annotations={
            "cert-manager.io/cluster-issuer": "letsencrypt-prod"
        }),
        spec=client.V1IngressSpec(
            ingress_class_name="nginx",
            rules=[
                client.V1IngressRule(
                    host=f"{subdomain}.{namespace}.ramanuj.dev",
                    http=client.V1HTTPIngressRuleValue(
                        paths=[
                            client.V1HTTPIngressPath(
                                path="/",
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name=f"{name}-svc",
                                        port=client.V1ServiceBackendPort(number=3000 if name == 'grafana' else 9090)
                                    )
                                )
                            )
                        ]
                    )
                )
            ],
            tls=[
                client.V1IngressTLS(
                    hosts=[f"{subdomain}.{namespace}.ramanuj.dev"],
                    secret_name=f"{name}-tls"
                )
            ]
        )
    )
    networking_api.create_namespaced_ingress(namespace=namespace, body=ingress)

@app.route('/create_stack', methods=['POST'])
def create_stack():
    data = request.json
    namespace = data.get('namespace')
    memory = data.get('memory')
    cpu = data.get('cpu')

    try:
        create_kubernetes_stack(namespace, memory, cpu)
        return jsonify({
            'status': 'success',
            'prometheus_ingress': f'prometheus.{namespace}.ramanuj.dev',
            'grafana_ingress': f'grafana.{namespace}.ramanuj.dev'
        })
    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)})

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
