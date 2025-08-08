{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "VARCHAR"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "IN",
        "operator": "IN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 0,
            "name": "$0",
            "type": "INTEGER"
          }
        ],
        "query": [
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
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 2,
                      "name": "$2",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Poland Ekstraklasa",
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
                    "table": "League",
                    "id": "0",
                    "inputs": []
                  }
                ]
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "Country",
          "id": "3",
          "inputs": []
        }
      ]
    }
  ]
}