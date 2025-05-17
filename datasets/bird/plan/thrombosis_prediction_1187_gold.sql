{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "INTEGER"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "GREATER_THAN_OR_EQUAL",
            "operator": ">=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 1,
                "name": "$1",
                "type": "DATE"
              },
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "DATE",
                "operands": [
                  {
                    "kind": "LITERAL",
                    "value": "1997-01-01",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              }
            ]
          },
          {
            "kind": "LESS_THAN_OR_EQUAL",
            "operator": "<=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 1,
                "name": "$1",
                "type": "DATE"
              },
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "DATE",
                "operands": [
                  {
                    "kind": "LITERAL",
                    "value": "1997-12-31",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              }
            ]
          },
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 7,
                "name": "$7",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Behcet",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "Examination",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}