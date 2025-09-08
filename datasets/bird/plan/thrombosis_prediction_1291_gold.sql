{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "DATE"
        },
        {
          "kind": "INPUT_REF",
          "index": 7,
          "name": "$7",
          "type": "FLOAT"
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
                    "index": 7,
                    "name": "$7",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 3.5,
                    "type": "DECIMAL",
                    "nullable": false,
                    "precision": 2
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
                    "index": 7,
                    "name": "$7",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 5.5,
                    "type": "DECIMAL",
                    "nullable": false,
                    "precision": 2
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
              "table": "Laboratory",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}