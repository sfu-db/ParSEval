{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 2,
      "name": "$2",
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
            "kind": "OTHER_FUNCTION",
            "operator": "SUBSTR",
            "type": "VARCHAR",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 4,
                "name": "$4",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": 1,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              },
              {
                "kind": "LITERAL",
                "value": 7,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          {
            "kind": "LITERAL",
            "value": "1970-10",
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
          "table": "Player",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}