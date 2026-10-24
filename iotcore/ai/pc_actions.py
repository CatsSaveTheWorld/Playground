"""Parameter-free automation choices shared by registry and execution."""
PC_AI_ACTIONS = {
    f'ai_{service}_{role}': {'service': service, 'role': role, 'label': f'{service.upper()} · {label}'}
    for service in ('asr', 'nlp', 'tts')
    for role, label in (('active', '액티브 지정'), ('standby', '스탠바이 지정'), ('off', '미지정 전환'))
}
