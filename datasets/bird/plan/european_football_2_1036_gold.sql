{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "INTEGER"
    }
  ],
  "aggs": [],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
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
                "kind": "GREATER_THAN",
                "operator": ">",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 4,
                    "name": "$4",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 50,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "LESS_THAN",
                "operator": "<",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 4,
                    "name": "$4",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 60,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
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
              "table": "Team_Attributes",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}