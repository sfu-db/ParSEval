{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 17,
      "name": "$17",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 18,
      "name": "$18",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 6,
      "name": "$6",
      "type": "VARCHAR"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "EQUALS",
        "operator": "=",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 10,
            "name": "$10",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "95203-3704",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
          }
        ]
      },
      "variableset": "[]",
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "schools",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}