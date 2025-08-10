# Argo Workflows Automation Script
# This script automates the submission and monitoring of Argo Workflows using Python.

import json
import time
import logging
import requests
from argo_workflows import ApiClient
from argo_workflows.configuration import Configuration
from argo_workflows.api.workflow_service_api import WorkflowServiceApi
from argo_workflows.models import (
    IoArgoprojWorkflowV1alpha1WorkflowSubmitRequest,
    IoArgoprojWorkflowV1alpha1SubmitOpts,
    IoArgoprojWorkflowV1alpha1WorkflowTemplateRef
)

ARGO_HOST = "https://your-argo-server.com"
MAX_RETRIES = 120  # 10 minutes at 5-second intervals

def setup_logging(log_level='info'):
    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s|%(levelname)s|[%(funcName)s:%(lineno)d]|%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger = logging.getLogger('main')
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False
    return logger

logger = setup_logging()

def configure_api_client(argo_token):
    configuration = Configuration(host=ARGO_HOST)
    configuration.api_key_prefix['BearerToken'] = 'Bearer'
    configuration.api_key['BearerToken'] = argo_token
    configuration.verify_ssl = False
    api_client = ApiClient(configuration)
    api_instance = WorkflowServiceApi(api_client=api_client)
    return configuration, api_client, api_instance

def parse_repo_name(repo_name):
    parts = repo_name.split('-', 2)
    if len(parts) >= 2:
        capability = parts[0]
        application = f"{parts[1]}-{parts[2]}" if len(parts) > 2 else parts[1]
    else:
        capability = repo_name
        application = repo_name
    return capability, application

def get_workflow_config(repo_name):
    capability, application = parse_repo_name(repo_name)
    namespace = f"{capability}-deploy-wf"
    workflow_template_name = f"{capability}-workload-staging"
    return {
        'namespace': namespace,
        'workflow_template_name': workflow_template_name,
        'workflow_url_base': f"{ARGO_HOST}/workflows/{namespace}"
    }

def get_deploy_settings(settings_json, branch='main'):
    try:
        if not settings_json:
            return {'app_branch': 'main'}
        settings = json.loads(settings_json)
        return settings.get(branch, settings.get('main', {'app_branch': 'main'}))
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing settings: {e}")
        return {'app_branch': 'main'}

def prepare_workflow_parameters(environment, application, app_branch='main'):
    return [
        f'environment={environment}',
        f'application={application}',
        'log_level=info'
    ]

def create_workflow_request(parameters, workflow_template_name):
    submit_opts = IoArgoprojWorkflowV1alpha1SubmitOpts(
        parameters=parameters,
        labels="workflows.argoproj.io/creator-email=automation@example.com"
    )
    template_ref = IoArgoprojWorkflowV1alpha1WorkflowTemplateRef(name=workflow_template_name)
    return IoArgoprojWorkflowV1alpha1WorkflowSubmitRequest(
        resource_kind="WorkflowTemplate",
        resource_name=workflow_template_name,
        submit_options=submit_opts,
        workflow_template_ref=template_ref
    )

def submit_workflow(api_instance, submit_request, namespace):
    try:
        api_response = api_instance.submit_workflow(
            namespace=namespace,
            body=submit_request,
            _check_return_type=False
        )
        workflow_name = api_response.metadata['name']
        workflow_namespace = api_response.metadata['namespace']
        logger.info(f"✨ Workflow submitted: {workflow_name} in {workflow_namespace}")
        return workflow_name, workflow_namespace, api_response
    except Exception as e:
        logger.error(f"Workflow submission failed: {e}")
        return None, None, None

def get_workflow_status(api_instance, namespace, workflow_name):
    return api_instance.get_workflow(namespace=namespace, name=workflow_name)

def poll_workflow_status(api_instance, workflow_name, namespace, workflow_url_base=None):
    logger.info("⏱️ Monitoring workflow progress...")
    retry_count = 0
    status_emojis = {
        "Running": "🔄", "Pending": "⏳",
        "Succeeded": "✅", "Failed": "❌", "Error": "⛔"
    }

    while retry_count < MAX_RETRIES:
        workflow_status = get_workflow_status(api_instance, namespace, workflow_name)
        if not workflow_status:
            time.sleep(5)
            retry_count += 1
            continue

        status_phase = workflow_status.status.get('phase', 'Unknown')
        emoji = status_emojis.get(status_phase, "❓")
        logger.info(f"Status: {emoji} {status_phase}")

        if status_phase in ["Succeeded", "Failed", "Error"]:
            return workflow_status

        time.sleep(5 if retry_count < 10 else 10)
        retry_count += 1

    logger.warning("⚠️ Max polling attempts reached")
    return None

def argoSubmit(argo_token, repo_name, environment, deploy_settings=None):
    try:
        workflow_config = get_workflow_config(repo_name)
        namespace = workflow_config['namespace']
        workflow_template_name = workflow_config['workflow_template_name']
        workflow_url_base = workflow_config['workflow_url_base']

        capability, application = parse_repo_name(repo_name)
        configuration, api_client, api_instance = configure_api_client(argo_token)

        app_branch = deploy_settings.get('app_branch', 'main') if deploy_settings else 'main'
        parameters = prepare_workflow_parameters(environment, application, app_branch)
        submit_request = create_workflow_request(parameters, workflow_template_name)

        workflow_name, namespace, api_response = submit_workflow(api_instance, submit_request, namespace)
        if workflow_name is None:
            return 1, '', ''

        final_status = poll_workflow_status(api_instance, workflow_name, namespace, workflow_url_base)
        workflow_url = f"{workflow_url_base}/{workflow_name}"

        if final_status:
            final_phase = final_status.status.get('phase', 'Unknown')
            if final_phase == 'Succeeded':
                logger.info("✅ Workflow completed successfully!")
                return 0, workflow_name, workflow_url
            elif final_phase in ['Running', 'Pending']:
                logger.warning("⏳ Workflow is still in progress")
                return 0, workflow_name, workflow_url
            else:
                logger.error(f"❌ Workflow failed with status: {final_phase}")
                return 1, workflow_name, workflow_url
        else:
            logger.error("❌ Workflow status could not be determined")
            return 1, workflow_name, workflow_url
    except Exception as e:
        logger.error(f"🔴 Unexpected error: {e}")
        return 1, '', ''

if __name__ == "__main__":
    import os

    # Example usage with environment variables
    argo_token = os.getenv("ARGO_TOKEN")
    repo_name = os.getenv("REPO_NAME", "api-service")
    environment = os.getenv("ENVIRONMENT", "staging")
    settings_json = os.getenv("AUTO_DEPLOY_SETTINGS", "")

    # Parse deployment settings
    deploy_settings = get_deploy_settings(settings_json)

    # Submit the workflow
    status_code, workflow_name, workflow_url = argoSubmit(
        argo_token, repo_name, environment, deploy_settings
    )

    # Output results
    print(f"Status Code: {status_code}")
    print(f"Workflow Name: {workflow_name}")
    print(f"Workflow URL: {workflow_url}")
