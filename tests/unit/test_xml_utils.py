#  Orchestration Engine - XML Plan Utilities Tests
#
#  Tests for extract_xml_plan() and parse_plan_xml() in xml_utils.py.
#
#  Depends on: backend/utils/xml_utils.py
#  Used by:    CI

from backend.utils.xml_utils import extract_xml_plan, parse_plan_xml


# --- extract_xml_plan tests ---


def test_extract_simple_plan():
    text = '<plan level="L1"><summary>Test</summary></plan>'
    assert extract_xml_plan(text) == text


def test_extract_with_preamble():
    text = 'Here is my plan:\n\n<plan level="L2"><summary>S</summary></plan>\n\nDone.'
    result = extract_xml_plan(text)
    assert result.startswith("<plan")
    assert result.endswith("</plan>")


def test_extract_with_markdown_fences():
    text = '```xml\n<plan level="L1"><summary>S</summary></plan>\n```'
    result = extract_xml_plan(text)
    assert result is not None
    assert "<summary>S</summary>" in result


def test_extract_with_thinking_block():
    text = '<thinking>Let me reason...</thinking>\n\n<plan level="L2"><summary>S</summary></plan>'
    result = extract_xml_plan(text)
    assert result.startswith("<plan")
    assert "<thinking>" not in result


def test_extract_no_plan_returns_none():
    assert extract_xml_plan("Just some text with no plan") is None


def test_extract_unclosed_plan_returns_none():
    assert extract_xml_plan('<plan level="L1"><summary>S</summary>') is None


# --- parse_plan_xml tests ---


_L1_XML = """<plan level="L1">
  <summary>Build a widget</summary>
  <tasks>
    <task index="0">
      <title>Create widget</title>
      <description>Build the widget component</description>
      <task_type>code</task_type>
      <complexity>medium</complexity>
      <depends_on></depends_on>
      <tools_needed>read_file,write_file</tools_needed>
      <requirement_ids>R1</requirement_ids>
      <verification_criteria>Widget renders</verification_criteria>
      <affected_files>src/widget.ts</affected_files>
    </task>
    <task index="1">
      <title>Test widget</title>
      <description>Add unit tests</description>
      <task_type>code</task_type>
      <complexity>simple</complexity>
      <depends_on>0</depends_on>
      <tools_needed>write_file</tools_needed>
      <requirement_ids>R1</requirement_ids>
      <verification_criteria>Tests pass</verification_criteria>
      <affected_files>tests/widget.test.ts</affected_files>
    </task>
  </tasks>
</plan>"""


def test_parse_l1_flat_tasks():
    result = parse_plan_xml(_L1_XML)
    assert result["summary"] == "Build a widget"
    assert len(result["tasks"]) == 2
    assert result["tasks"][0]["title"] == "Create widget"
    assert result["tasks"][0]["task_type"] == "code"
    assert result["tasks"][0]["depends_on"] == []
    assert result["tasks"][0]["tools_needed"] == ["read_file", "write_file"]
    assert result["tasks"][0]["requirement_ids"] == ["R1"]
    assert result["tasks"][0]["affected_files"] == ["src/widget.ts"]
    assert result["tasks"][1]["depends_on"] == [0]


_L2_XML = """<plan level="L2">
  <summary>Build auth system</summary>
  <phases>
    <phase name="Foundation">
      <description>Set up core auth infrastructure</description>
      <task index="0">
        <title>User model</title>
        <description>Define user table</description>
        <task_type>code</task_type>
        <complexity>medium</complexity>
        <depends_on></depends_on>
        <tools_needed>write_file</tools_needed>
        <requirement_ids>R1</requirement_ids>
        <verification_criteria>Migration runs</verification_criteria>
        <affected_files>db/models.py</affected_files>
      </task>
    </phase>
    <phase name="Integration">
      <description>Wire auth into API</description>
      <task index="1">
        <title>Auth middleware</title>
        <description>JWT validation</description>
        <task_type>code</task_type>
        <complexity>complex</complexity>
        <depends_on>0</depends_on>
        <tools_needed>read_file,write_file</tools_needed>
        <requirement_ids>R2,R3</requirement_ids>
        <verification_criteria>Auth tests pass</verification_criteria>
        <affected_files>src/middleware.py</affected_files>
      </task>
    </phase>
  </phases>
  <questions>
    <question>
      <ask>Use JWT or sessions?</ask>
      <proposed>JWT for stateless auth</proposed>
      <impact>Sessions would need Redis</impact>
    </question>
  </questions>
</plan>"""


def test_parse_l2_phased():
    result = parse_plan_xml(_L2_XML)
    assert result["summary"] == "Build auth system"
    assert len(result["phases"]) == 2
    assert result["phases"][0]["name"] == "Foundation"
    assert result["phases"][0]["description"] == "Set up core auth infrastructure"
    assert len(result["phases"][0]["tasks"]) == 1
    assert result["phases"][1]["tasks"][0]["depends_on"] == [0]
    assert result["phases"][1]["tasks"][0]["requirement_ids"] == ["R2", "R3"]


def test_parse_l2_open_questions():
    result = parse_plan_xml(_L2_XML)
    assert len(result["open_questions"]) == 1
    q = result["open_questions"][0]
    assert q["question"] == "Use JWT or sessions?"
    assert q["proposed_answer"] == "JWT for stateless auth"
    assert q["impact"] == "Sessions would need Redis"


_L3_XML = """<plan level="L3">
  <summary>Payment integration</summary>
  <phases>
    <phase name="Core">
      <description>Payment processing</description>
      <task index="0">
        <title>Stripe client</title>
        <description>Wrap Stripe API</description>
        <task_type>code</task_type>
        <complexity>complex</complexity>
        <depends_on></depends_on>
        <tools_needed>write_file</tools_needed>
        <requirement_ids>R1</requirement_ids>
        <verification_criteria>API calls succeed</verification_criteria>
        <affected_files>src/stripe.py</affected_files>
      </task>
    </phase>
  </phases>
  <questions>
    <question>
      <ask>Which payment provider?</ask>
      <proposed>Stripe</proposed>
      <impact>Different SDK</impact>
    </question>
  </questions>
  <risks>
    <risk>
      <description>Stripe rate limits during peak</description>
      <likelihood>low</likelihood>
      <impact>high</impact>
      <mitigation>Implement retry with exponential backoff</mitigation>
    </risk>
  </risks>
  <test_strategy>
    <approach>Mock Stripe API in tests</approach>
    <test_tasks>Stripe client,Payment flow</test_tasks>
    <coverage_notes>Cover refund edge cases</coverage_notes>
  </test_strategy>
</plan>"""


def test_parse_l3_risks():
    result = parse_plan_xml(_L3_XML)
    assert len(result["risk_assessment"]) == 1
    r = result["risk_assessment"][0]
    assert r["risk"] == "Stripe rate limits during peak"
    assert r["likelihood"] == "low"
    assert r["impact"] == "high"
    assert "backoff" in r["mitigation"]


def test_parse_l3_test_strategy():
    result = parse_plan_xml(_L3_XML)
    ts = result["test_strategy"]
    assert ts["approach"] == "Mock Stripe API in tests"
    assert ts["test_tasks"] == ["Stripe client", "Payment flow"]
    assert "refund" in ts["coverage_notes"]


_CSHARP_XML = """<plan level="csharp">
  <summary>Implement user service</summary>
  <phases>
    <phase name="UserService">
      <description>Core user operations</description>
      <task index="0">
        <title>UserService.GetUser</title>
        <description>Fetch user by ID</description>
        <task_type>csharp_method</task_type>
        <complexity>medium</complexity>
        <depends_on></depends_on>
        <target_class>MyApp.Services.UserService</target_class>
        <target_signature>public async Task&lt;User&gt; GetUser(Guid id)</target_signature>
        <available_methods>Save(User u),Delete(Guid id)</available_methods>
        <constructor_params>IDbContext db,ILogger logger</constructor_params>
        <requirement_ids>R1</requirement_ids>
        <verification_criteria>Returns user or throws</verification_criteria>
        <affected_files>src/Services/UserService.cs</affected_files>
      </task>
    </phase>
  </phases>
  <questions>
    <question>
      <ask>Use nullable return or exception?</ask>
      <proposed>Exception for not found</proposed>
      <impact>Changes caller error handling</impact>
    </question>
  </questions>
  <assembly_config>
    <new_files>src/Services/UserService.cs</new_files>
    <modified_files>src/DI/Container.cs</modified_files>
  </assembly_config>
</plan>"""


def test_parse_csharp_plan():
    result = parse_plan_xml(_CSHARP_XML)
    task = result["phases"][0]["tasks"][0]
    assert task["task_type"] == "csharp_method"
    assert task["target_class"] == "MyApp.Services.UserService"
    assert "Task<User>" in task["target_signature"]  # XML entity decoded
    assert task["constructor_params"] == ["IDbContext db", "ILogger logger"]
    assert task["available_methods"] == ["Save(User u)", "Delete(Guid id)"]


def test_parse_csharp_assembly_config():
    result = parse_plan_xml(_CSHARP_XML)
    ac = result["assembly_config"]
    assert ac["new_files"] == ["src/Services/UserService.cs"]
    assert ac["modified_files"] == ["src/DI/Container.cs"]


def test_parse_depends_on_multiple():
    xml = """<plan level="L1"><summary>S</summary><tasks>
    <task index="2"><title>T</title><description>D</description>
    <depends_on>0,1</depends_on></task>
    </tasks></plan>"""
    result = parse_plan_xml(xml)
    assert result["tasks"][0]["depends_on"] == [0, 1]


def test_parse_empty_depends_on():
    xml = """<plan level="L1"><summary>S</summary><tasks>
    <task index="0"><title>T</title><description>D</description>
    <depends_on></depends_on></task>
    </tasks></plan>"""
    result = parse_plan_xml(xml)
    assert result["tasks"][0]["depends_on"] == []


def test_parse_missing_optional_fields():
    xml = """<plan level="L1"><summary>S</summary><tasks>
    <task index="0"><title>T</title><description>D</description></task>
    </tasks></plan>"""
    result = parse_plan_xml(xml)
    t = result["tasks"][0]
    assert t["tools_needed"] == []
    assert t["affected_files"] == []
    assert t["depends_on"] == []
    assert t["task_type"] == "code"
    assert t["complexity"] == "medium"


def test_parse_xml_entities():
    """XML entities like &lt; and &amp; are decoded properly."""
    xml = """<plan level="L1"><summary>S</summary><tasks>
    <task index="0"><title>T</title>
    <description>Use &lt;T&gt; and &amp; operator</description></task>
    </tasks></plan>"""
    result = parse_plan_xml(xml)
    assert result["tasks"][0]["description"] == "Use <T> and & operator"


def test_roundtrip_dict_shape():
    """Parsed XML dict has the same keys as what JSON planner produces."""
    result = parse_plan_xml(_L2_XML)
    # Must have these top-level keys
    assert "summary" in result
    assert "phases" in result
    assert "open_questions" in result
    # Each phase must have these keys
    phase = result["phases"][0]
    assert "name" in phase
    assert "description" in phase
    assert "tasks" in phase
    # Each task must have these keys
    task = phase["tasks"][0]
    for key in ("title", "description", "task_type", "complexity",
                "depends_on", "tools_needed", "requirement_ids",
                "verification_criteria", "affected_files"):
        assert key in task, f"Missing key: {key}"
