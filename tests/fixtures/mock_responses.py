"""Mock responses and test constants for the test suite."""

VALID_LITERATURE_JSON = '''{
    "summary": "This comprehensive literature review explores recent advances in machine learning and IoT network traffic classification, synthesizing findings from 15 peer-reviewed sources published between 2022-2024.",
    "papers": [
        {
            "paper_name": "Deep Learning for Network Traffic Classification",
            "types_of_available_data": "packet headers, payload samples, timing information",
            "reproducible": "yes",
            "complexity": "medium",
            "privacy_preservation": "yes"
        }
    ]
}'''

INVALID_LITERATURE_JSON = '''{
    "summary": "ok",
    "papers": []
}'''

BROKEN_JSON = '{this is not valid json at all'

EMPTY_RESPONSE = ''

ERROR_RESPONSE = 'Error: rate limit exceeded'

VALID_SUPERVISOR_JSON = '''{
    "executive_summary": "Significant progress observed across all research projects this quarter. Three projects have reached key milestones, while two require additional focus on methodology refinement.",
    "evaluations": [
        {
            "project_name": "Test_Research_Project",
            "status": "ON_TRACK",
            "notes": "Project is progressing as planned with consistent updates."
        }
    ]
}'''

VALID_KEYWORDS = 'machine learning IoT network traffic classification'

STANFORD_REVIEW_TEXT = '''Summary: The paper presents a novel approach to applying deep learning techniques for real-time network traffic classification in IoT environments. The authors propose a convolutional neural network architecture that achieves 96% accuracy on benchmark datasets while maintaining computational efficiency suitable for edge devices.

Strengths: 
- Novel architecture design optimized for resource-constrained environments
- Comprehensive evaluation on multiple datasets with statistical significance testing
- Clear presentation with well-designed figures and tables
- Reproducible research with code availability promised
- Practical applicability to production IoT systems

Weaknesses:
- Limited discussion of adversarial robustness and potential evasion techniques
- Evaluation limited to specific IoT protocols; generalization unclear
- Comparison with recent baselines could be more comprehensive
- Runtime analysis focuses on inference but lacks training time discussion
- Dataset bias not adequately addressed for different network topologies
''' * 2  # Repeated to ensure length > 400

MOCK_SEMANTIC_SCHOLAR_RESULTS = [
    {
        "title": "Deep Learning for Network Traffic Classification",
        "link": "https://semanticscholar.org/paper/abc123",
        "snippet": "This paper presents a deep learning approach to classify network traffic in IoT environments using convolutional neural networks.",
        "year": "2024",
        "citationCount": "15",
        "venue": "IEEE IoT Journal"
    },
    {
        "title": "Security in IoT Communication",
        "link": "https://semanticscholar.org/paper/def456",
        "snippet": "We survey current security challenges and solutions in IoT communication networks.",
        "year": "2023",
        "citationCount": "42",
        "venue": "ACM Computing Surveys"
    },
    {
        "title": "Machine Learning for Cybersecurity",
        "link": "https://semanticscholar.org/paper/ghi789",
        "snippet": "A comprehensive review of machine learning applications in detecting and preventing cyber threats.",
        "year": "2024",
        "citationCount": "28",
        "venue": "Computers & Security"
    }
]
